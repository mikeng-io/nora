"""Dreamer store admission, shared by cursor, evidence, input and commit phases."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class _Lease:
    slots: asyncio.Semaphore
    held: bool = False
    deadline: asyncio.Timeout | None = None

    async def acquire(self) -> None:
        deadline = self.deadline
        end = deadline.when() if deadline is not None else None
        remaining = None if end is None else end - asyncio.get_running_loop().time()
        if deadline is not None:
            deadline.reschedule(None)
        try:
            await self.slots.acquire()
            self.held = True
        finally:
            if deadline is not None and remaining is not None:
                deadline.reschedule(asyncio.get_running_loop().time() + remaining)

    def release(self) -> None:
        if self.held:
            self.held = False
            self.slots.release()


_current: ContextVar[_Lease | None] = ContextVar('dream_store_lease', default=None)


@asynccontextmanager
async def store_unit(slots: asyncio.Semaphore) -> AsyncIterator[_Lease]:
    lease = _Lease(slots)
    token = _current.set(lease)
    try:
        await lease.acquire()
        yield lease
    finally:
        lease.release()
        _current.reset(token)


@asynccontextmanager
async def store_phase() -> AsyncIterator[None]:
    lease = _current.get()
    acquired = lease is not None and not lease.held
    if acquired and lease is not None:
        await lease.acquire()
    try:
        yield
    finally:
        if acquired and lease is not None:
            lease.release()


@asynccontextmanager
async def model_phase() -> AsyncIterator[None]:
    lease = _current.get()
    if lease is not None:
        lease.release()
    try:
        yield
    finally:
        # Cancellation must not wait for another admission in order to unwind.
        task = asyncio.current_task()
        if lease is not None and task is not None and not task.cancelling():
            await lease.acquire()
