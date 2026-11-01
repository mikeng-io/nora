package bridge

// Real-Redis proofs for the frozen enqueue algorithm. These run ONLY when
// NORA_TEST_REDIS_URL points at a disposable Redis (docker); without it they
// skip — a skipped integration test is reported, never claimed.

import (
	"context"
	"fmt"
	"os"
	"testing"

	"github.com/redis/go-redis/v9"
)

func testRedis(t *testing.T) (*Queue, *redis.Client) {
	t.Helper()
	url := os.Getenv("NORA_TEST_REDIS_URL")
	if url == "" {
		t.Skip("NORA_TEST_REDIS_URL unset; no disposable Redis")
	}
	acct := fmt.Sprintf("acct-%d", os.Getpid())
	q, err := NewQueue(url, "instagram", acct, "nora-v1")
	if err != nil {
		t.Fatal(err)
	}
	opts, _ := redis.ParseURL(url)
	rc := redis.NewClient(opts)
	t.Cleanup(func() {
		rc.Del(context.Background(), q.stream)
		rc.Del(context.Background(), "nq:dedupe:v1:a", "nq:dedupe:v1:b", "nq:dedupe:v2:a-rot")
		iter := rc.Scan(context.Background(), 0, "nq:src:"+q.stream+":*", 100).Iterator()
		for iter.Next(context.Background()) {
			rc.Del(context.Background(), iter.Val())
		}
		q.Close()
		rc.Close()
	})
	return q, rc
}

// Same event twice → one entry, original id returned. This is the
// lost-response duplicate: the second call is indistinguishable from a
// retry after the first XADD's answer was lost.
func TestRedisDedupeLostResponse(t *testing.T) {
	q, rc := testRedis(t)
	ctx := context.Background()
	if err := q.EnsureGroup(ctx); err != nil {
		t.Fatal(err)
	}
	src := SourceKey("instagram", "acct", "room-1", "message", "mid:m1")
	id1, canon1, err := q.Enqueue(ctx, []byte(`{"v":1}`), src, "v1:a")
	if err != nil {
		t.Fatal(err)
	}
	id2, canon2, err := q.Enqueue(ctx, []byte(`{"v":1}`), src, "v1:a")
	if err != nil {
		t.Fatal(err)
	}
	if id1 != id2 || canon1 != "v1:a" || canon2 != "v1:a" {
		t.Fatalf("duplicate returned (%q,%q) vs (%q,%q) — second entry created", id1, canon1, id2, canon2)
	}
	if n := rc.XLen(ctx, q.stream).Val(); n != 1 {
		t.Fatalf("stream length = %d, want 1", n)
	}
}

// Rotation: same source_key under a NEW key version resolves to the ORIGINAL
// stream id AND the original alias — one stream entry, and every downstream
// effect (receipt, action id) keys on the canonical alias.
func TestRedisRotationResolvesOriginal(t *testing.T) {
	q, rc := testRedis(t)
	ctx := context.Background()
	if err := q.EnsureGroup(ctx); err != nil {
		t.Fatal(err)
	}
	src := SourceKey("instagram", "acct", "room-1", "message", "mid:m2")
	id1, canon1, err := q.Enqueue(ctx, []byte(`{"v":1,"eid":"v1:a"}`), src, "v1:a")
	if err != nil {
		t.Fatal(err)
	}
	// Key rotated: the replay carries a different event_id alias.
	id2, canon2, err := q.Enqueue(ctx, []byte(`{"v":1,"eid":"v2:a-rot"}`), src, "v2:a-rot")
	if err != nil {
		t.Fatal(err)
	}
	if id1 != id2 {
		t.Fatalf("rotated replay created a second entry: %q vs %q", id1, id2)
	}
	if canon2 != "v1:a" || canon1 != "v1:a" {
		t.Fatalf("canonical alias = %q/%q, want original v1:a for both", canon1, canon2)
	}
	if n := rc.XLen(ctx, q.stream).Val(); n != 1 {
		t.Fatalf("stream length = %d, want 1", n)
	}
	// The src mapping must keep naming the ORIGINAL alias so even the first
	// alias keeps resolving after rotation.
	if got := rc.Get(ctx, q.src+q.stream+":"+src).Val(); got != "v1:a" {
		t.Fatalf("src mapping = %q, want original v1:a", got)
	}
	// A SECOND retry under the same rotated alias resolves src-first again:
	// still the original stream id and canonical alias, still one entry.
	id3, canon3, err := q.Enqueue(ctx, []byte(`{"v":1,"eid":"v2:a-rot"}`), src, "v2:a-rot")
	if err != nil {
		t.Fatal(err)
	}
	if id3 != id1 || canon3 != "v1:a" {
		t.Fatalf("re-rotated retry = (%q,%q), want (%q,v1:a)", id3, canon3, id1)
	}
}

// Distinct legitimate transitions are NOT suppressed by the dedupe: two
// sources → two entries, each with its own id.
func TestRedisDistinctTransitionsRetained(t *testing.T) {
	q, rc := testRedis(t)
	ctx := context.Background()
	if err := q.EnsureGroup(ctx); err != nil {
		t.Fatal(err)
	}
	s1 := SourceKey("instagram", "acct", "r", "reaction", "react:m:fb:❤️:add:1")
	s2 := SourceKey("instagram", "acct", "r", "reaction", "react:m:fb:❤️:remove:2")
	i1, c1, err := q.Enqueue(ctx, []byte(`{"t":1}`), s1, "v1:a")
	if err != nil {
		t.Fatal(err)
	}
	i2, c2, err := q.Enqueue(ctx, []byte(`{"t":2}`), s2, "v1:b")
	if err != nil {
		t.Fatal(err)
	}
	if i1 == i2 || c1 != "v1:a" || c2 != "v1:b" || rc.XLen(ctx, q.stream).Val() != 2 {
		t.Fatalf("distinct transitions collapsed: ids %q/%q canon %q/%q len=%d",
			i1, i2, c1, c2, rc.XLen(ctx, q.stream).Val())
	}
}
