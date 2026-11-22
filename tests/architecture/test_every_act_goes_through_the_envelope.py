"""Outward client calls must have an inventoried executor or a non-act use."""

# ruff: noqa: E501
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from nora.memory.domain.act_executors import EXECUTORS

NORA = Path(__file__).resolve().parents[2] / "nora"

CLIENT_METHODS = {
    "delivery": {
        "send", "send_message", "send_text", "sendmail", "sendall", "sendto",
        "send_message_to_user", "send_str", "send_json", "send_bytes", "post_comment",
        "sendMessage", "sendReaction", "react", "add_reaction", "remove_reaction", "typing",
        "edit_message", "edit_original_response", "delete_message", "defer", "edit", "sync",
    },
    "http": {"post", "put", "patch", "delete", "request", "ws_connect", "urlopen"},
    "broker": {"xadd", "publish"},
    "mcp": {"stream", "call_tool", "call_tool_async", "call_tool_sync"},
    "process": {
        "create_subprocess_exec", "create_subprocess_shell", "Popen", "check_call",
        "check_output", "system", "execve", "spawnve",
    },
    "startup": {"bulk_upsert_global_commands", "bulk_upsert_guild_commands"},
}
_METHODS = frozenset.union(*(frozenset(value) for value in CLIENT_METHODS.values()))
_DESTINATION_TABLES = {"DOCUMENTS", "DOCUMENT_REVISION", "DOCUMENT_STATE", "MUSIC_QUEUE"}
_NETWORK_NAMESPACES = {"urllib", "http", "email"}


def _import_key(name: str) -> str:
    root, _, _ = name.partition(".")
    return name if root in _NETWORK_NAMESPACES else root


class Source(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.imports: set[str] = set()
        self.calls: Counter[str] = Counter()
        self.executors: set[str] = set()
        self.edges: set[tuple[str, str]] = set()

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.update(_import_key(alias.name) for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and not node.level:
            if node.module in _NETWORK_NAMESPACES:
                self.imports.update(_import_key(f"{node.module}.{a.name}") for a in node.names)
            else:
                self.imports.add(_import_key(node.module))

    def _nested(self, node) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _nested  # noqa: N815 - AST visitor protocol
    visit_AsyncFunctionDef = _nested  # noqa: N815 - AST visitor protocol
    visit_ClassDef = _nested  # noqa: N815 - AST visitor protocol

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and node.value in EXECUTORS:
            self.executors.add(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        callee = node.func
        if isinstance(callee, ast.Attribute):
            self._attribute_call(callee, node)
        elif isinstance(callee, ast.Name) and callee.id in _METHODS:
            self.calls[f"{'.'.join(self.stack)} {callee.id}"] += 1
        self.generic_visit(node)

    def _attribute_call(self, callee: ast.Attribute, node: ast.Call) -> None:
        name = ast.unparse(callee)
        where = ".".join(self.stack)
        self.edges.add((where, name))
        first = ast.unparse(node.args[0]) if node.args else ""
        if callee.attr == "import_module" and node.args:
            module = node.args[0]
            if isinstance(module, ast.Constant) and isinstance(module.value, str):
                self.imports.add(_import_key(module.value))
        destination = (
            name in {"sa.insert", "sa.update", "sa.delete"} and first in _DESTINATION_TABLES
        )
        process = name in {"subprocess.run", "subprocess.call", "os.execv", "os.execl"}
        if callee.attr in _METHODS or destination or process:
            # Request selectors and executable names distinguish the writes and reads
            # that happen to use the same transport method.
            selector = first if destination or callee.attr in CLIENT_METHODS["http"] else ""
            key = f"{where} {name} {selector}".strip()
            self.calls[key] += 1


def _read_sources() -> dict[str, Source]:
    result = {}
    for path in sorted(NORA.rglob("*.py")):
        source = Source()
        source.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        result[path.relative_to(NORA).as_posix()] = source
    return result


# Newly imported libraries are reviewed before they can reach an outward client.
IMPORTED_LIBRARIES = frozenset(['PIL',
 '__future__',
 'aiohttp',
 'alembic',
 'argparse',
 'ast',
 'asyncio',
 'base64',
 'binascii',
 'bisect',
 'boto3',
 'botocore',
 'collections',
 'contextlib',
 'contextvars',
 'copy',
 'cryptography',
 'csv',
 'dataclasses',
 'datetime',
 'decimal',
 'errno',
 'discord',
 'enum',
 'fcntl',
 'fnmatch',
 'functools',
 'gzip',
 'hashlib',
 'hmac',
 'html',
 'httpx',
 'httpx2',
 'importlib',
 'inspect',
 'io',
 'ipaddress',
 'itertools',
 'json',
 'jsonschema',
 'jwt',
 'logging',
 'math',
 'mimetypes',
 'mypy_boto3_s3',
 'nora',
 'openai',
 'opentelemetry',
 'os',
 'pathlib',
 'platform',
 'pgvector',
 'prometheus_client',
 'pydantic',
 'pydantic_core',
 'pypdf',
 'random',
 're',
 'redis',
 'resource',
 'secrets',
 'sentry_sdk',
 'shlex',
 'shutil',
 'signal',
 'socket',
 'sqlalchemy',
 'sqlite3',
 'ssl',
 'stat',
 'strands',
 'structlog',
 'subprocess',
 'sys',
 'tempfile',
 'threading',
 'time',
 'tomlkit',
 'tomllib',
 'traceback',
 'types',
 'typing',
 'unicodedata',
 'unittest',
 'urllib.error',
 'urllib.parse',
 'urllib.request',
 'uuid',
 'xml',
 'yaml',
 'yarl',
 'yt_dlp',
 'zipfile',
 'zlib',
 'zoneinfo'])

ORY_OWN_RECORDS = {
    # Keto and Kratos hold Nora's identity and access records in her own deployment.
    "infra/ory/http.py": {"request session.request method": 1},
    "infra/ory/keto.py": {
        "KetoClient.check request": 1, "KetoClient.put_tuple request": 1,
        "KetoClient.drop_tuple request": 1, "KetoClient.tuples_on request": 1,
        "KetoClient.namespaces request": 1,
    },
    "infra/ory/kratos.py": {
        "KratosClient.create_identity request": 1, "KratosClient.get_identity request": 1,
        "KratosClient.get_by_external_id request": 1, "KratosClient.set_state request": 1,
        "KratosClient.set_display_name request": 1, "KratosClient.drop_sessions request": 2,
    },
}

# This inventory includes transport reads, custody and other non-act uses.
KNOWN_CALLS = {'agent/comprehend_tools.py': {'ComprehendRead.stream self._inner.stream': 1,
                               'HistoricalRead.stream self._inner.stream': 1},
 'agent/computer_ctx.py': {'_cctx__make_blob_row._computer_blob c.blob_store.put': 1},
 'agent/fetch_watch.py': {'WatchedTool.stream self._inner.stream': 1},
 'agent/guards.py': {'Deliverer.deliver send': 1,
                     'Deliverer.deliver.send adapter.send': 1,
                     'Deliverer.react react': 1,
                     'Deliverer.react.react self._adapter.react': 1},
 'agent/mcp.py': {'stream_mounted envelope.stream': 1, 'stream_mounted tool.stream': 1},
 'agent/room_routing.py': {'RoomRoutedTool.stream target.stream': 1},
 'agent/tool_deps.py': {'ToolDeps._keep_fetched_media self.blob_store.put': 1},
 'app/cli.py': {'cmd_profile_history sa.delete W': 1},
 'app/compute_poller.py': {'ComputeJobPoller._keep self._blobs.put': 1},
 'app/computer_queue.py': {"_lifecycle_work lifecycle.request 'acquire'": 1,
                           "_lifecycle_work lifecycle.request 'release'": 1},
 'app/computer_task_acts.py': {'ComputerTaskActs._publish self._redis.xadd': 1},
 'app/social_expiry.py': {'StoryMediaExpiry._delete self._blobs.delete': 1},
 'app/social_poller.py': {'SocialPoller._keep self._blobs.put': 1},
 'app/summons.py': {'SummonsOccasion._answer self._client.post card.shortcode': 1,
                    'SummonsOccasion._flush self._client.post_comment': 1},
 'computer/adapters/tools/export.py': {'_computer_export__stage_file c.blob_store.put': 1},
 'computer/adapters/tools/sandbox.py': {'_make_computer_edit.computer_edit runtime.edit': 1},
 'computer/adapters/tools/transfer.py': {'_copy_in_put runtime.put': 1},
 'computer/wire.py': {"_scoped_computer_queues._reconcile_scope lifecycle.request 'observe'": 1},
 'conversation/adapters/tools/replies.py': {'_make_reaction_send.reaction_send deliverer.react': 1,
                                            '_make_send_to.send_to relay.send': 1},
 'documents/adapters/tools.py': {'_document_publish__publish c.documents.publish': 1,
                                 '_make_session_document_revise.document_revise service.publish': 1},
 'engagement/stages/indicate.py': {'_react.call react': 1},
 'infra/computer/browser.py': {'CdpConnection._request self._ws.send_str': 1,
                               'connect_browser_cdp.connect session.ws_connect ws_url': 1,
                               'open_browser session.ws_connect ws_url': 1},
 'infra/computer/client.py': {"ComputerdRuntime._post_json session.post f'{self._base}/{op}'": 1,
                              "ComputerdRuntime.fetch.body session.post f'{self._base}/fetch'": 1,
                              "ComputerdRuntime.put.body session.post f'{self._base}/put'": 1},
 'infra/computer/lifecycle.py': {"HostLifecycleClient._request session.post 'http://localhost/lifecycle'": 1},
 'infra/computer/manager.py': {'ScopeBrowserManager._stop_guest s.post url': 1,
                               "ScopeBrowserManager._sweep_scope self._lifecycle.request 'observe'": 1,
                               "ScopeBrowserManager._sweep_segment self._lifecycle.request 'idle-stop'": 1,
                               'ScopeBrowserManager.capture self._runtime.put': 1},
 'infra/computer/probe.py': {'ComputerdProbe._answered session.post self._url': 1},
 'infra/computer/scoped.py': {'ScopedComputerdRuntime.edit rt.edit': 1,
                              'ScopedComputerdRuntime.put rt.put': 1},
 'infra/embedder.py': {'LlamaEmbedder._call session.request method': 1},
 'infra/external_files/google_drive.py': {'GoogleDriveSource._get client.stream': 1},
 'infra/external_files/parse.py': {'parse_isolated asyncio.create_subprocess_exec': 1},
 'infra/external_files/text.py': {'_Package.targets self.stream': 1,
                                  '_docx pkg.stream': 1,
                                  '_pptx pkg.stream': 2,
                                  '_xlsx pkg.stream': 3},
 'infra/geocode.py': {'CachingBilingualGeocoder.address_for self._store.put': 1},
 'infra/google/auth.py': {'GoogleCredentials._refresh client.stream': 1},
 'infra/honcho/client.py': {'HonchoClient._claim session.put url': 1,
                            'HonchoClient._join_scope session.post url': 1,
                            'HonchoClient._post session.post url': 1,
                            'HonchoClient.delete_session_in session.delete url': 1,
                            'HonchoClient.refresh_card_in session.post url': 1,
                            'HonchoClient.search session.post url': 1,
                            'HonchoClient.session_holds_messages_in session.post url': 1,
                            'HonchoClient.session_messages session.post url': 1},
 'infra/image_gen/openai_codex_adapter.py': {"OpenAICodexImageGenerator._post session.post f'{self._url}/{endpoint}'": 1},
 'infra/image_gen/openai_codex_oauth.py': {'OpenAICodexOAuth._exchange session.post self._token_url': 1,
                                           "device_flow_login session.post f'{accounts}/deviceauth/token'": 1,
                                           "device_flow_login session.post f'{accounts}/deviceauth/usercode'": 1,
                                           "device_flow_login session.post f'{base}/oauth/token'": 1},
 'infra/image_gen/xai_adapter.py': {'XaiImageGenerator.generate session.post url': 1},
 'infra/image_gen/xai_oauth.py': {'XaiOAuth._exchange session.post self._token_endpoint': 1,
                                  'device_flow_login session.post device_code_url': 1,
                                  'device_flow_login session.post token_endpoint': 1},
 'infra/life360/client.py': {'Life360Client._authenticate request': 1,
                             'Life360Client._get request': 1,
                             'Life360Client._get_json request': 1,
                             'Life360Client._post_request request': 1},
 'infra/life360/http.py': {'_blocking_request urllib.request.urlopen req': 1},
 'infra/presence_refresh.py': {'HttpLocationRefresher.refresh session.post self._url': 1},
 'infra/routes/http.py': {'post_json session.post url': 1},
 'infra/search/exa.py': {'ExaSearchProvider._post session.post url': 1},
 'infra/search/tavily.py': {'TavilySearchProvider._fetch session.post self._extract_url': 1,
                            'TavilySearchProvider._search session.post self._url': 1},
 'infra/social/bridge_client.py': {"OpenCliBridgeClient._read session.post f'{self._bridge_url}/read'": 1},
 'infra/social/bridge_server.py': {'_spawn_opencli asyncio.create_subprocess_exec': 1},
 'infra/social/session_probe/alerts.py': {'FanOutSink.send s.send': 1},
 'infra/social/session_probe/probe.py': {'Probe._emit_alerts self._sink.send': 1},
 'infra/social/session_probe/runner.py': {'subprocess_runner subprocess.run': 1},
 'infra/social/summons_client.py': {'InstagramSummonsClient._call session.request method': 1},
 'infra/speech.py': {'SenseVoiceTranscriber.transcribe session.post self._url': 1},
 'infra/sql/aggregate_writes.py': {'AggregateWrites.discard_rows sa.delete table': 1},
 'infra/sql/documents.py': {'SqlDocumentStore.add_revision sa.insert DOCUMENT_REVISION': 1,
                            'SqlDocumentStore.create sa.insert DOCUMENTS': 1,
                            'SqlDocumentStore.create sa.insert DOCUMENT_REVISION': 1,
                            'SqlDocumentStore.set_state sa.insert DOCUMENT_STATE': 1},
 'infra/sql/item_retention.py': {'tombstone_items sa.delete table': 1},
 'infra/sql/graph_entry_about.py': {'_about_relation sa.delete LEGACY_RELATION_EVIDENCE': 1},
 'infra/sql/history.py': {'append_only_as_of conn.stream': 1, 'as_of conn.stream': 1},
 'infra/sql/memory_index.py': {'_delete sa.delete MEMORY_NODE': 1,
                               '_delete_edges sa.delete MEMORY_EDGE': 1,
                               '_drop_episodes sa.delete MEMORY_NODE': 1,
                               '_drop_orphan_hubs sa.delete MEMORY_NODE': 1,
                               '_guarded sa.delete MEMORY_PROJECTION_QUEUE': 1,
                               'apply_index_image sa.delete MEMORY_EDGE': 1,
                               'apply_index_image sa.delete MEMORY_NODE': 1,
                               'apply_index_image sa.delete MEMORY_PROJECTION_QUEUE': 1},
 'infra/sql/music_queue.py': {'SqlMusicQueueStore._clear sa.delete MUSIC_QUEUE': 1,
                              'SqlMusicQueueStore._enqueue sa.insert MUSIC_QUEUE': 1,
                              'SqlMusicQueueStore._move_to_front sa.update MUSIC_QUEUE': 2,
                              'SqlMusicQueueStore._pop_next sa.delete MUSIC_QUEUE': 1},
 'infra/sql/music_session.py': {'SqlMusicSessionStore.delete sa.delete MUSIC_SESSION': 1},
 'infra/sql/observation_heads.py': {'SqlObservationHeadWriter._retire sa.delete ST': 1},
 'infra/sql/presence.py': {'PresenceStore._write_place_heads sa.delete table': 1},
 'infra/sql/snapshot_at.py': {'_copy_history src.stream': 1},
 'infra/sql/stimuli.py': {'SqlStimulusStore.record_outcome sa.delete STIMULUS_PENDING': 1,
                          'SqlStimulusStore.record_wake sa.delete STIMULUS_PENDING': 1,
                          'SqlStimulusStore.retract sa.delete STIMULUS_PENDING': 1,
                          'SqlStimulusStore.retract_self sa.delete STIMULUS_PENDING': 1},
 'infra/sql/tag_debounce.py': {'SqlTagDebounceStore.prune_before sa.delete TAG_DEBOUNCE_STATE': 1},
 'infra/transit/catalogue.py': {'get_text session.post url': 1},
 'infra/tts.py': {"MiniMaxTtsSpeaker._attempt client.post host + '/v1/t2a_v2'": 1},
 'infra/video_gen.py': {'MiniMaxVideoBackend._fetch_clip client.stream': 1,
                        "MiniMaxVideoBackend.start client.post f'{self._host}/v2/video_generation'": 1},
 'infra/video_refs.py': {'_run asyncio.create_subprocess_exec': 1},
 'infra/vision/cache.py': {'VisionCache.aget self.put key': 1,
                           'VisionCache.aput self.put key': 1},
 'infra/vision/preprocess.py': {'_run asyncio.create_subprocess_exec': 1,
                                '_run_video asyncio.create_subprocess_exec': 1},
 'infra/voice/http_controller.py': {'VoiceControllerClient._call session.request method': 1},
 'infra/voice/hub.py': {'VoiceControllerHub.stream_speech self._client_for(guild_id).stream': 1},
 'ingest/stages/media.py': {'_take blobs.put': 1},
 'ingest/stickers.py': {'StickerReader._read self._blobs.put': 1},
 'media/adapters/tools/create.py': {'_generate_image__store_blob c.blob_store.put': 1},
 'media/adapters/tools/fetch.py': {'_media_fetch__answer c.video_readings.put': 1},
 # Released claims discard their unrun internal journal rows in the settlement transaction.
 'memory/adapters/proactive_claims.py': {'_settle_claims sa.delete DEBRIEF_OBLIGATION': 1},
 'memory/adapters/stored_row_registration.py': {'SqlStoredRows._register_aggregates PRESENCE_PLACES.delete': 1},
 'platform/bridge/client.py': {"BridgeAdapter._post session.post f'{self._endpoint.base_url}{path}'": 1,
                               "BridgeAdapter._put_bytes session.put f'{self._endpoint.base_url}{path}'": 1},
 'platform/bridge/projection.py': {'BridgeChannelDirectory.refresh session.post self._url': 1},
 'platform/discord/adapter.py': {'DiscordAdapter.react self._reactor.react': 1,
                                 'DiscordAdapter.send self._sender.send': 1},
 'platform/discord/bridge_app.py': {'DiscordRestActionService.run bridge._reactor.react': 1,
                                    'DiscordRestActionService.run bridge._sender.send': 1,
                                    'DiscordRestActionService.run channel.typing': 1,
                                    'DiscordRestActionService.run message.edit': 1},
 'platform/discord/bridge_control.py': {"BridgeControlClient._music session.post f'{self._endpoint.base_url}/v1/music/{op}'": 1,
                                        "BridgeControlClient._speech session.post f'{self._endpoint.base_url}/v1/voice/{guild_id}/speech'": 1,
                                        "BridgeControlClient.read session.post f'{self._endpoint.base_url}/v1/reads'": 1,
                                        'BridgeDiscordAdapter.react self._delegate.react': 1},
 'platform/discord/command_acts.py': {'DiscordCommandActs.denied interaction.response.send_message': 1,
                                      'DiscordCommandActs.respond interaction.followup.send': 1,
                                      'DiscordCommandActs.respond interaction.response.defer': 1,
                                      'DiscordCommandActs.respond interaction.response.edit_message': 1,
                                      'DiscordCommandActs.respond interaction.response.send_message': 1,
                                      'DiscordCommandActs.sync tree.sync': 1,
                                      'DiscordCommandActs.timeout message.edit': 1},
 'platform/discord/music_commands.py': {'register (command_acts or DiscordCommandActs()).sync': 1},
 'platform/discord/music_manager.py': {'MusicPlayerManager.discard_persisted_session self._session_store.delete guild_id': 1},
 'platform/discord/music_notify.py': {'_send channel.send': 1, 'make_notify._post send': 2},
 'platform/discord/music_player.py': {'MusicPlayer.finish_pending self._session.delete self._guild_id': 1,
                                      'MusicPlayer.stop self._session.delete self._guild_id': 1},
 'platform/discord/react.py': {'DiscordReactor._request session.request method': 1},
 'platform/discord/send.py': {"DiscordSender._post session.post f'{self._base_url}/channels/{post_channel}/messages'": 2},
 'platform/instagram/adapter.py': {'InstagramAdapter.react self._reactor.react': 1,
                                   'InstagramAdapter.send self._sender.send': 1},
 'platform/instagram/react.py': {"InstagramReactor._put session.put f'{self._base_url}{reaction_path(conversation_id, message_id)}'": 1},
 'platform/instagram/send.py': {"InstagramSender._post session.post f'{self._base_url}{messages_path(conversation_id)}'": 1},
 'platform/signal/adapter.py': {'SignalAdapter.react self._reactor.react': 1,
                                'SignalAdapter.send self._sender.send': 1},
 'platform/signal/directory.py': {'_rpc session.post url': 1},
 'platform/signal/media.py': {'SignalMediaSource.fetch session.post url': 1},
 'platform/signal/react.py': {"SignalReactor._rpc session.post f'{self._base_url}/api/v1/rpc'": 1},
 'platform/signal/self_id.py': {'_rpc_batch session.post url': 1},
 'platform/signal/send.py': {"SignalSender._rpc session.post f'{self._base_url}/api/v1/rpc'": 1,
                             'SignalSender.send self.send': 1},
 'platform/whatsapp/adapter.py': {'WhatsAppAdapter.react self._reactor.react': 1,
                                  'WhatsAppAdapter.send self._sender.send': 1},
 'platform/whatsapp/react.py': {"WhatsAppReactor._post session.post f'{self._base_url}/react'": 1},
 'platform/whatsapp/send.py': {"WhatsAppSender._post session.post f'{self._base_url}{path}'": 1},
 'social/adapters/tools/web.py': {'_web_fetch__guards c.blob_store.delete': 1,
                                  '_web_fetch__guards c.blob_store.put': 1,
                                  '_web_fetch__guards c.fetched_pages.put': 1},
 'subject/context_filter.py': {'exposure patch': 2,
                               "flash client.post url.rstrip('/') + '/chat/completions'": 1},
 'subject/eval_evidence.py': {'revision.git subprocess.check_output': 1},
 'subject/lexical_seed.py': {'_load sa.delete MESSAGES': 1},
 'subject/memory_workspace.py': {'HonchoWorkspaces._call session.request method': 1,
                                 'draw_workspace honcho.delete workspace': 1,
                                 'sweep_stale_draw_workspaces honcho.delete workspace': 1},
 'subject/sandbox_index.py': {'index_sandbox cache.put cache_key': 1},
 'testing/fakes.py': {'FakeSandboxRuntime.edit self.put': 1,
                      'FakeSandboxRuntime.write_text self.put': 1},
 'testing/signal_daemon.py': {'FakeSignalDaemon.emit self._events.put event': 1},
 'web/otp.py': {'register_otp_routes.request_code otp.request': 1},
 'app/composition_onboarding.py': {"_signal_bridge_fetch.fetch session.post f'{endpoint.base_url}/v1/reads'": 1},
 'infra/sql/managed_handoff.py': {'complete_import sa.delete MANAGED_IDENTITY_HOLD': 1},
 'infra/sql/managed_identity.py': {'SqlBindingStore.release_identity_hold sa.delete MANAGED_IDENTITY_HOLD': 1,
                                   'SqlBindingStore.clear_block sa.delete MANAGED_AUTHORITY_BLOCK': 1},
 'infra/sql/managed_publication.py': {'drop_membership_support sa.delete MANAGED_MEMBERSHIP_SUPPORT': 1,
                                      'retract_membership_support sa.delete MANAGED_MEMBERSHIP_SUPPORT': 1,
                                      '_merge_membership_rows sa.delete MANAGED_MEMBERSHIP': 1,
                                      '_merge_membership_rows sa.delete MANAGED_MEMBERSHIP_SUPPORT': 1,
                                      'consolidate_people sa.delete MANAGED_AUTHORITY_BLOCK': 1,
                                      'consolidate_people sa.delete MANAGED_IDENTITY_HOLD': 1,
                                      'consolidate_people sa.delete MANAGED_SCOPED_NAME': 1,
                                      'consolidate_people sa.delete MANAGED_PERSON': 1},
 'people/app/holder_link.py': {'HolderLinkService.request_link PendingLink.request operation': 1},
 'platform/signal/consent.py': {'SignalLinkDelivery.deliver.send self._sender.send': 1,
                               'SignalLinkDelivery.deliver send': 1},
 'subject/environment.py': {'IsolatedServices._start_redis asyncio.create_subprocess_exec': 1},
 **ORY_OWN_RECORDS,
}


def test_every_act_goes_through_the_envelope():
    sources = _read_sources()
    unknown = {name: source.imports - IMPORTED_LIBRARIES for name, source in sources.items()
               if source.imports - IMPORTED_LIBRARIES}
    added = {
        name: dict(source.calls - Counter(KNOWN_CALLS.get(name, {})))
        for name, source in sources.items()
        if source.calls - Counter(KNOWN_CALLS.get(name, {}))
    }
    assert not unknown and not added, (unknown, added)

    claimed = set.union(*(
        source.executors for name, source in sources.items()
        if name != "memory/domain/act_executors.py"
    ))
    assert claimed == set(EXECUTORS)


ENVELOPE_ENTRYPOINTS = {
    "browser.connect": ("infra/computer/browser.py", "connect_browser_cdp", "effects.invoke"),
    "browser.mutation": ("infra/computer/browser.py", "CdpConnection.call", "self._effects.invoke"),
    "browser.stop": ("infra/computer/manager.py", "ScopeBrowserManager._browser_stop_guest",
                     "self._effects.invoke"),
    "bridge.delivery": ("platform/bridge/service.py", "DurableActionService._invoke_action",
                        "self._effects.invoke"),
    "bridge.typing": ("platform/bridge/service.py", "DurableActionService._invoke_action",
                      "self._effects.invoke"),
    "bridge.music": ("platform/discord/bridge_control.py", "BridgeControlClient.music",
                     "self._effects.invoke"),
    "bridge.speech": ("platform/discord/bridge_control.py", "BridgeControlClient.speech",
                      "self._effects.invoke"),
    "computer.dispatch": ("app/computer_task_acts.py", "ComputerTaskActs.start",
                          "self._envelope.invoke"),
    "computer.lifecycle": ("infra/computer/lifecycle.py", "HostLifecycleClient.request",
                           "self._effects.invoke"),
    "computer.run": ("infra/computer/client.py", "ComputerdRuntime._guarded", "self._effects.invoke"),
    "computer.workspace": ("infra/computer/client.py", "ComputerdRuntime._guarded",
                           "self._effects.invoke"),
    "discord.command_denial": ("platform/discord/command_acts.py", "DiscordCommandActs.denied",
                               "self._execute"),
    "discord.command_reply": ("platform/discord/command_acts.py", "DiscordCommandActs.respond",
                              "self._execute"),
    "discord.command_sync": ("platform/discord/command_acts.py", "DiscordCommandActs.sync",
                             "self._execute"),
    "discord.music_notice": ("platform/discord/command_acts.py", "DiscordCommandActs.notice",
                             "self._envelope.perform"),
    "document.write": ("app/documents_service.py", "DocumentService._write", "self._writes.invoke"),
    "mcp.invoke": ("agent/mcp.py", "stream_mounted", "envelope.stream"),
    "music.queue": ("infra/sql/music_queue.py", "SqlMusicQueueStore._write", "self._effects.invoke"),
    "platform.otp": ("agent/guards.py", "Deliverer.deliver", "self._delivery_acts.run"),
    "platform.progress": ("engagement/stages/indicate.py", "_react", "acts.run"),
    "platform.reaction": ("agent/guards.py", "Deliverer.react", "self._delivery_acts.run"),
    "platform.reply": ("agent/guards.py", "Deliverer.deliver", "self._delivery_acts.run"),
    "signal.private_link": ("platform/signal/consent.py", "SignalLinkDelivery.deliver",
                            "self._envelope.perform"),
    "subagent.launch": ("app/subagent_backend.py", "SubagentBackend.launch", "self._acts.invoke"),
    "voice.control": ("infra/voice/http_controller.py", "VoiceControllerClient._control",
                      "self._effects.invoke"),
    "voice.speech": ("infra/voice/http_controller.py", "VoiceControllerClient._control",
                     "self._effects.invoke"),
}


def test_registered_executors_enter_the_envelope_from_their_production_paths():
    assert set(ENVELOPE_ENTRYPOINTS) == set(EXECUTORS)
    sources = _read_sources()
    for executor, (path, scope, call) in ENVELOPE_ENTRYPOINTS.items():
        assert executor in sources[path].executors, executor
        assert (scope, call) in sources[path].edges, executor


def test_mounted_mcp_invocation_has_one_executor():
    sources = _read_sources()
    streams = {
        f"{name}:{key}"
        for name, source in sources.items()
        for key in source.calls
        if key.endswith(" tool.stream") or key.endswith(".call_tool_async")
    }
    assert streams == {"agent/mcp.py:stream_mounted tool.stream"}
