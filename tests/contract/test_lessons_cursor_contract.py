"""A Lessons session resumes the persisted consolidation cursor."""

from nora.agent.dream_screen import offence
from nora.domain.culture import Episode
from nora.domain.lessons import CharacterRoom
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.language import SqlLanguageStore
from nora.infra.sql.lessons import SqlLessonStore
from nora.infra.sql.schema import CONSOLIDATION_WATERMARK
from nora.language.lesson_book import LessonBook
from nora.language.lessons import CognitionLessons, LessonEvidence, LessonsKey
from nora.testing import InMemoryCharacterEvidence


async def test_lessons_resumes_and_advances_the_existing_watermark(clean_pg_url):
    unit = "cursor-test"
    keys = {unit: LessonsKey(unit=unit, rooms=(CharacterRoom(
        key="test-room", platform="signal", label="Test room",
    ),))}

    def clock_ms() -> int:
        return 1_790_000_000_000

    engine = create_sql_engine(clean_pg_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(CONSOLIDATION_WATERMARK.insert().values(
                consolidator_name="lessons", scope_id=unit, last_message_id=3,
            ))
        episodes = InMemoryCharacterEvidence()
        for send_id in range(1, 7):
            episodes.add_episode(unit=unit, episode=Episode(
                send_id=send_id, room="test-room", text=f"Generated episode {send_id}",
                outcome="unanswered", reactions=0, edits=0,
            ))
        book = LessonBook(
            store=SqlLessonStore(engine), law=lambda: (), screen=offence, clock_ms=clock_ms,
        )
        cursor = SqlLanguageStore(engine)
        lessons = CognitionLessons(
            evidence=LessonEvidence(store=episodes, book=book, keys=keys, clock_ms=clock_ms),
            book=book, keys=keys, cursor=cursor, batch_limit=20, horizon_seconds=604_800,
            name_of=lambda person_id: "someone", clock_ms=clock_ms,
        )

        view = await lessons.shown(unit=unit)
        assert [episode.send_id for episode in view.episodes] == [4, 5, 6]
        await lessons.take(unit=unit, view=view)
        assert await cursor.watermark_for(consolidator_name="lessons", scope_id=unit) == 6
    finally:
        await engine.dispose()
