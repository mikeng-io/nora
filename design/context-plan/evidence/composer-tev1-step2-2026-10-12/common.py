"""Shared pieces of the Tev1 composer benchmark (design/context-plan/15, step 2).

Runs on norty from ~/composer-labels/step2 (python3 stdlib only). It imports the
first pass's extract.py and aggregate.py from the parent directory
(evidence/composer-labels-2026-10-12/ in git) for the token fit, the n-gram
overlap and the greeting rule, so both passes measure sizes and overlap the
same way.

Three things live here:
  state_of(r)      what a composer could know BEFORE the turn runs (§4 State)
  evidence(r)      the mechanical ground truth: what the turn actually did
  bundle maps      §4's D1 options -> units, the base bundle, the floor

No judge, no labels: evidence is tool calls, media codes and string overlap
between the reply/tool args and a block. It is positive evidence only.
"""
import datetime, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.expanduser("~/composer-labels")
sys.path.insert(0, PARENT)
import aggregate as AG  # noqa: E402  (first pass: grams, tok, is_greeting, FRAMING_TOK)

TURNS = os.path.join(PARENT, "turns")
AUG = os.path.join(HERE, "aug")  # replying_to + sender_listed per turn (prep.py), room text: norty only
HKT = datetime.timezone(datetime.timedelta(hours=8))
CJK_CHAR = re.compile(r"[㐀-鿿　-〿＀-￯]")

# ---------------------------------------------------------------- units
FLOOR_UNITS = {"runtime_context", "tools.resident.send_reply"}  # §3; header/message/core are in floor_tokens
BASE = {  # §4 base bundle, every turn
    "doctrine.voice.typing", "doctrine.voice.not_writing", "doctrine.voice.not_nora", "doctrine.voice.one_message",
    "doctrine.conduct.judgement", "doctrine.conduct.follow", "block.lessons",
}
CAT = "tools.catalogue"
BUNDLES = {  # §4 D1 table, in the recorded window's unit ids; a bundle with deferred tools brings the catalogue (§4a rule 2)
    "small_talk": set(),
    "lookup": {"doctrine.room_law.found", "doctrine.conduct.answer", "doctrine.conduct.look_first",
               "doctrine.conduct.honesty", "tools.resident.web_search", "tools.resident.web_fetch",
               "tools.resident.now", CAT},
    "person": {"doctrine.room_law.others_words", "doctrine.conduct.person", "block.peer_card", "block.peer_history",
               "block.character", "block.room_members", "block.self_card", "tools.resident.search_memory", CAT},
    "group_plans": {"block.standing", "block.standing_working_set", "block.notes", "block.memos",
                    "doctrine.conduct.sorting_out", "doctrine.conduct.records", "doctrine.room_law.writes",
                    "tools.resident.note_read", CAT},
    "media": {"doctrine.room_law.pictures", "doctrine.pictures", CAT},
    "work": {"block.documents", "block.computer", "block.subagents", "doctrine.conduct.document",
             "doctrine.room_law.can_do", "skills.available", CAT},
    "banter": {"doctrine.conduct.banter", "doctrine.conduct.judging", "doctrine.voice.swearing",
               "doctrine.voice.jokes", "doctrine.room_law.others_words", "block.culture", "block.unit_culture",
               "block.character", "block.self"},
    "correction": {"doctrine.room_law.told_wrong", "doctrine.room_law.found", "doctrine.conduct.contradicted",
                   "doctrine.voice.told_wrong"},
    "about_nora": {"doctrine.identity.who_you_are", "doctrine.identity.where_you_come_from",
                   "doctrine.identity.what_you_are", "doctrine.belonging.place", "doctrine.belonging.why",
                   "doctrine.belonging.serve", "block.self", "block.lessons", "block.presence",
                   "doctrine.room_law.can_do"},
    "music": {CAT},
}
D1_CRITERIA = {
    "small_talk": "a greeting, thanks, or chit-chat that needs no fact, record or tool",
    "lookup": "a fact to find out: weather, a place, transit, a price, opening hours, news, a link's content",
    "person": "about someone in the room or the sender themself: what they said, did, like, or where they are",
    "group_plans": "the group's plans, who owes what, what was decided, a reminder or a note to keep",
    "media": "a picture, clip, sticker, file or voice note that was sent and needs looking at",
    "work": "a document to write or read, Nora's computer, a delegated job, or an image or video to make",
    "banter": "joking, teasing, roasting, or being asked to judge who is right",
    "correction": "someone says Nora is wrong, or disputes a fact she gave",
    "about_nora": "about Nora herself: who she is, what she can do, where she is, how she is",
    "music": "play, skip, stop or change the volume of music in a voice channel",
}
D1_INSTR = ("Nora is an AI member of a Hong Kong friends' group chat (mostly Cantonese). Somebody just tagged her: "
            "`message` is what they wrote, `replying_to` the message they replied to, `recent` the last lines of the "
            "chat before it (oldest first), `facts` what the system knows for certain. "
            "What does answering this message involve?")
D2_CRITERIA = {
    "last_few": "the message and the last few lines are enough",
    "window": "she needs the last twenty or so lines of the conversation",
    "extended": "she needs a long stretch of the conversation, or what happened around an older quoted message",
}
D2_INSTR = ("Same chat and message. How much of the conversation before this message does Nora need to read "
            "to answer it well?")
NOUL_INSTR = ("Nora is an AI member of a Hong Kong friends' group chat (mostly Cantonese). Somebody just tagged her: "
              "`message` is what they wrote, `replying_to` the message they replied to, `recent` the last lines of "
              "the chat before it (oldest first), `facts` what the system knows for certain. Her prompt for "
              "answering is assembled from parts. Does answering this message need the part described in `part`? "
              "(In a part's description, \"you\" means Nora.)")
NOUL_CRITERIA = {"true": "The answer would go worse without this part: a wrong or missing fact, a broken rule, a worse "
                         "tone, context missed, or a tool she could not call.",
                 "false": "The part is only loosely related to the message, or not involved at all."}

# §4a rule 1: thresholds by the cost of a miss
def part_class(uid):
    if uid.startswith("doctrine.room_law."):
        return "law"
    if uid.startswith("block."):
        return "memory"
    if uid.startswith("tools.") or uid == "skills.available":
        return "tools"
    return "practice"
THRESH = {"law": 0.10, "memory": 0.20, "tools": 0.20, "practice": 0.35}
DEPS = {  # §4a rule 2 (parts that exist in the recorded window)
    "tools.resident.web_search": {"doctrine.room_law.found"},
    "tools.resident.web_fetch": {"doctrine.room_law.found"},
    "tools.resident.now": {"doctrine.room_law.found"},
    "block.peer_history": {"block.peer_card"},
    "block.standing_working_set": {"block.standing"},
    "block.documents": {"doctrine.conduct.document"},
}
MEDIA_OVERRIDE = {"doctrine.room_law.pictures", "doctrine.pictures", CAT}  # §4a rule 4

# ---------------------------------------------------------------- evidence
RESIDENT = {"now", "search_memory", "web_search", "web_fetch", "note_read", "send_reply"}
TOOL_BUNDLE = {}
for b, names in {
    "lookup": "web_search web_fetch place_info now weather route calculate reverse_geocode event_search "
              "find_parking parking_status instagram_read pulse_list_servers pulse_game_progress "
              "pulse_squad_activity pulse_player_profile pulse_recent_events",
    "group_plans": "note_read note_update remind remember recall_lookups list_events create_event update_event "
                   "standing_read",
    "media": "media_fetch file_fetch media_search",
    "work": "document_read document_publish document_set_status generate_image video_create",
    "person": "about search_memory ask_memory lookup_person character_read group_members where_is where_been "
              "online_now saved_places routine_read",
    "music": "play_music stop_music skip_music set_music_volume music_status top_music",
}.items():
    for n in names.split():
        TOOL_BUNDLE[n] = b
TOOL_PARTS = {  # parts a call implies beyond its schema/catalogue
    "note_read": {"block.notes"}, "note_update": {"block.notes", "doctrine.conduct.records"},
    "remind": {"block.notes", "doctrine.conduct.records"}, "remember": {"block.notes", "doctrine.conduct.records"},
    "recall_lookups": {"block.notes"}, "create_event": {"doctrine.conduct.records"},
    "update_event": {"doctrine.conduct.records"}, "standing_read": {"block.standing"},
    "media_fetch": {"doctrine.pictures"}, "file_fetch": {"doctrine.pictures"}, "media_search": {"doctrine.pictures"},
    "document_read": {"block.documents", "doctrine.conduct.document"},
    "document_publish": {"block.documents", "doctrine.conduct.document"},
    "document_set_status": {"block.documents", "doctrine.conduct.document"},
    "lookup_person": {"block.room_members"}, "group_members": {"block.room_members"},
    "web_search": {"doctrine.room_law.found"}, "web_fetch": {"doctrine.room_law.found"},
}
BLOCK_BUNDLE = {
    "block.peer_card": "person", "block.peer_history": "person", "block.character": "person",
    "block.room_members": "person", "block.self_card": "person",
    "block.notes": "group_plans", "block.memos": "group_plans", "block.standing": "group_plans",
    "block.standing_working_set": "group_plans",
    "block.documents": "work", "block.computer": "work", "block.subagents": "work",
    "block.self": "about_nora", "block.presence": "about_nora",
    "block.culture": "banter", "block.unit_culture": "banter",
}
IGNORED_TOOLS = {"send_reply", "load_tools", "skills", "send_to", "load_transcript"}


def tool_bundle(n):
    if n.startswith(("computer_", "subagent_", "document_")):
        return "work"
    if n.startswith("memo_"):
        return "group_plans"
    if n.startswith(("where_",)):
        return "person"
    return TOOL_BUNDLE.get(n)


def tool_parts(n):
    p = set(TOOL_PARTS.get(n, ()))
    p.add("tools.resident." + n if n in RESIDENT else CAT)
    if n.startswith("computer_"):
        p.add("block.computer")
    return p


def inner(el):
    return re.sub(r"<[^>]*>", "", el or "").strip()


def line_rec(l):
    m = re.match(r"<m\b([^>]*)>", l)
    a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1))) if m else {}
    return {"from": a.get("from"), "age": a.get("age"), "text": inner(l)[:400]}


def reply_text(r):
    return " ".join(r["replies"]) + " " + (r["final_text"] if not r["replies"] else "")


def evidence(r, aug):
    """List of evidence items {bundle, parts (present only), src} + depth evidence."""
    items, unknown_tools = [], []
    for t in r["tools"]:
        n = t["name"]
        if n in IGNORED_TOOLS:
            continue
        b = tool_bundle(n)
        if b is None:
            unknown_tools.append(n)
            continue
        items.append({"bundle": b, "parts": sorted(p for p in tool_parts(n) if p in r["units"]), "src": "tool:" + n})
    if r["media_codes"]:
        items.append({"bundle": "media", "parts": sorted(p for p in {"doctrine.pictures"} if p in r["units"]),
                      "src": "media_code"})
    # block reuse: >=3 distinctive n-grams shared by the reply/tool args and a block, not seen in
    # the message, transcript, replying_to or doctrine (aggregate.mech's rule)
    out = reply_text(r) + " " + " ".join(t["args"] for t in r["tools"])
    go = AG.grams(out)
    seen = AG.grams(r["message"] + " ".join(r["transcript"]["last15"]) + (aug.get("replying_to") or ""))
    for uid, u in r["units"].items():
        if uid.startswith("doctrine"):
            seen |= AG.grams(u["text"] or "")
    for uid, u in r["units"].items():
        if uid in BLOCK_BUNDLE and u.get("text"):
            ov = (go & AG.grams(u["text"])) - seen
            if len(ov) >= 3:
                items.append({"bundle": BLOCK_BUNDLE[uid], "parts": [uid], "src": "overlap:" + uid})
    # one item per (bundle, source): five web_search calls are one piece of evidence
    uniq = {}
    for i in items:
        uniq.setdefault((i["bundle"], i["src"]), i)
    items = list(uniq.values())
    # depth: does the reply reuse a line older than the last 5 (of the 15 recorded)?
    depth = "unknown"
    if any(t["name"] == "load_transcript" for t in r["tools"]):
        depth = "extended"
    else:
        lines = r["transcript"]["last15"]
        old, recent = lines[:-5], lines[-5:]
        seen2 = AG.grams(r["message"] + " ".join(recent) + (aug.get("replying_to") or ""))
        for uid, u in r["units"].items():
            if not uid.startswith("doctrine") and u.get("text"):
                seen2 |= AG.grams(u["text"])
        for l in old:
            if len((AG.grams(reply_text(r)) & AG.grams(l)) - seen2) >= 3:
                depth = "beyond_last5"
                break
    return items, depth, unknown_tools


# ---------------------------------------------------------------- state (§4)
def cjk_share(r):
    t = re.sub(r"@\S+", "", inner(r["message"]))
    t = re.sub(r"\s+", "", t)
    return (len(CJK_CHAR.findall(t)) / len(t)) if t else 0.0


def state_of(r, aug):
    ts = datetime.datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).astimezone(HKT)
    m = re.match(r"<tag_message\b([^>]*)>", r["message"] or "")
    a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1))) if m else {}
    return {
        "message": {"from": a.get("from"), "text": inner(r["message"])[:1500]},
        "replying_to": aug.get("replying_to_rec"),
        "recent": [line_rec(l) for l in r["transcript"]["last15"][-5:]],
        "facts": {
            "spoken": bool(r["units"].get("block.spoken_turn")),
            "media_in_recent": r["media_codes"],
            "hour": ts.hour, "weekday": ts.strftime("%A"),
            "room_has_documents": "block.documents" in r["units"],
            "room_has_computer_session": "block.computer" in r["units"],
            "room_has_running_jobs": "block.subagents" in r["units"],
            "room_has_open_standing_items": "block.standing_working_set" in r["units"],
            "sender_is_listed_person": aug.get("sender_listed", False),
        },
    }


def load_turns():
    import glob
    out = []
    for f in glob.glob(TURNS + "/*.json"):
        r = json.load(open(f))
        ap = os.path.join(AUG, r["id"] + ".json")
        out.append((r, json.load(open(ap)) if os.path.exists(ap) else {}))
    out.sort(key=lambda x: x[0]["ts"], reverse=True)
    return out
