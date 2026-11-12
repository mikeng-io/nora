# Room directory and history

## Two disclosure levels

`room_list` answers which rooms of this unit exist and how active they are.
It returns labels, networks, the current-room marker, transport state,
roster counts where readable, human message and distinct-speaker activity,
and the other-groups existence flag. It returns no message text, member
names or conversation identifiers.

`room_read` answers what people said in one of those rooms. Its candidates
come only from the current unit: an exact returned label, or a network when
it identifies exactly one candidate. An unknown label and another unit's
label produce the same no-match result, with no echo that would distinguish
them. The current conversation is never substituted for the requested target.

Direct chats and missing directory wiring have their own unavailable or
nothing-found semantics; they are not an empty group. A roster or activity
read that cannot be checked reports that limitation rather than a zero count.
The tools remain read-only and their internal unit check applies on every
call, including Dreamer use; there is no unit-safe bypass.

## Activity and routing

Activity comes from the Record, not a capped transcript fetch. It counts
human rows and distinct resolved speakers over the declared window, excluding
Nora. The newest human timestamp answers freshness independently of the
count.

`group_members` remains the tool for who is in the current room; room listing
is for rooms and activity, not a replacement membership roster. `send_to`
uses the same room model but retains its own policy and live-health checks.

Presence answers which rooms exist without a tool. `room_list` earns its
call when member, speaker or message activity is needed. `room_read` earns
its call when the actual words are needed. A busiest-room result note may
answer directly, but it never turns a message count into a speaker count or
causes unnecessary transcript reads by conflating them.

## Readable time and pagination

Transcript rows use the same readable calendar and age vocabulary as the
prefilled transcript, including edit time and the target room's clock context.
Raw epoch handles do not cross the model boundary. A full page reports when
older records exist instead of appearing complete.

An optional `before` reads the newest rows strictly before a supplied moment.
A date or partial timestamp includes the person's stated span; malformed or
ambiguous zones never silently return the newest page. The result's note
supplies the next exact cursor only when more records exist.

Pagination excludes the cursor point and does not split a same-timestamp tie
group across pages. This prevents both duplicates and unreachable rows;
only a tie group larger than the page remains an explicit coverage limit.
Empty earlier reads mean nothing is recorded before that point, not that the
parser fell back to now.

Store and fake implement the same scoped query, over-fetch and edit fold.
Budgets still bound page count, and `room_read` returns other people's words
as untrusted data with the ordinary media join and no instruction authority.
