package httptransport

// The inbound limits that are about TIME and NUMBER: how long one request body
// may take to arrive, how many bodies are read at once, how long one event
// stream may live, how long a stream may stay blocked, and how many may be open.
// They run over a real socket, because a recorder can neither stall nor be cut
// off, and a limit that is never exercised is a limit that is not there.

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"instagram-bridge/internal/app"
	"instagram-bridge/internal/stream"
)

// listen serves the real routes with the given event hub on an ephemeral port.
func listen(t *testing.T, hub Events) *httptest.Server {
	t.Helper()
	ts := httptest.NewServer(New(app.New(fakeSession{authenticated}, failingPorts{}, failingPorts{}), hub, time.Now()).Routes())
	t.Cleanup(ts.Close)
	return ts
}

// setDuration swaps a package limit for one test and restores it afterwards.
func setDuration(t *testing.T, p *time.Duration, v time.Duration) {
	t.Helper()
	prev := *p
	*p = v
	t.Cleanup(func() { *p = prev })
}

// A client that declares a body and then stops sending it holds a goroutine and
// a body slot for as long as it likes, unless the body has a deadline. The
// deadline answers 400: what arrived is not a request.
func TestAStalledRequestBodyIsCutOff(t *testing.T) {
	setDuration(t, &bodyReadTimeout, 150*time.Millisecond)
	ts := listen(t, stream.NewHub())
	conn, err := net.Dial("tcp", strings.TrimPrefix(ts.URL, "http://"))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	fmt.Fprintf(conn, "POST /threads/%s/messages HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{\"text\":", room)
	_ = conn.SetReadDeadline(time.Now().Add(3 * time.Second))
	reply, err := bufio.NewReader(conn).ReadString('\n')
	if err != nil || !strings.Contains(reply, " 400 ") {
		t.Fatalf("reply %q (err %v); want a 400 once the body deadline has passed", reply, err)
	}
}

// Each body read holds a slot. A full set of slots answers 503 at once, with
// the envelope's `unavailable`, and the request is retryable: it goes through
// as soon as a slot is free.
func TestTheBodyReadersAreBounded(t *testing.T) {
	for i := 0; i < maxBodyReaders; i++ {
		bodySlots <- struct{}{}
	}
	t.Cleanup(func() {
		for len(bodySlots) > 0 {
			<-bodySlots
		}
	})
	h := handler(authenticated, nil)
	rec := do(h, http.MethodPost, "/threads/"+room+"/messages", `{"text":"hi"}`)
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("with every slot held, status = %d, want 503 (%s)", rec.Code, rec.Body.String())
	}
	if code := body(t, rec)["error"].(map[string]any)["code"]; code != "unavailable" {
		t.Fatalf("code = %v, want unavailable", code)
	}
	for len(bodySlots) > 0 {
		<-bodySlots
	}
	if rec := do(h, http.MethodPost, "/threads/"+room+"/messages", `{"text":"hi"}`); rec.Code == http.StatusServiceUnavailable {
		t.Fatalf("with the slots free, status is still 503")
	}
}

// The body deadline is per request body, not a timeout on the connection: an
// event stream is a GET that never reads a body, and must outlive it. This is
// the guard against a blanket timeout being added later.
func TestAnEventStreamOutlivesTheBodyDeadline(t *testing.T) {
	setDuration(t, &bodyReadTimeout, 50*time.Millisecond)
	hub := stream.NewHub()
	ts := listen(t, hub)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, ts.URL+"/events", nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	time.Sleep(300 * time.Millisecond) // several times the body deadline
	hub.Emit(map[string]any{"kind": "message", "n": 1})
	reader := bufio.NewReader(resp.Body)
	for {
		line, err := reader.ReadString('\n')
		if err != nil {
			t.Fatalf("stream ended before the event arrived: %v", err)
		}
		if strings.HasPrefix(line, "data: ") {
			return
		}
	}
}

// Past maxClients an event stream is refused with 503 and the refusal is
// counted on /health, where an operator reads the bridge's state.
func TestAnExcessEventStreamIsRefusedAndCounted(t *testing.T) {
	hub := stream.NewHub()
	for i := 0; i < 8; i++ { // stream.maxClients
		if _, _, _, err := hub.Subscribe(); err != nil {
			t.Fatal(err)
		}
	}
	ts := listen(t, hub)
	resp, err := http.Get(ts.URL + "/events")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("the excess stream answered %d, want 503", resp.StatusCode)
	}
	health, err := http.Get(ts.URL + "/health")
	if err != nil {
		t.Fatal(err)
	}
	defer health.Body.Close()
	var got map[string]any
	if err := json.NewDecoder(health.Body).Decode(&got); err != nil {
		t.Fatal(err)
	}
	if got["rejected_clients"] != float64(1) {
		t.Fatalf("/health rejected_clients = %v, want 1", got["rejected_clients"])
	}
}

// A stream has a lifetime. The handler ends it and the client reconnects, and
// the reconnect is replayed what was buffered in the gap, so nothing is lost.
func TestAnEventStreamEndsAtItsLifetime(t *testing.T) {
	setDuration(t, &streamMaxLifetime, 100*time.Millisecond)
	ts := listen(t, stream.NewHub())
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, ts.URL+"/events", nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if _, err := io.ReadAll(resp.Body); err != nil {
		t.Fatalf("the stream did not end at its lifetime: %v", err)
	}
}

// A client that stops reading is the one that blocks a write forever. Each
// frame has a deadline, so such a client is disconnected and its slot freed.
func TestAStreamThatStopsReadingIsDisconnected(t *testing.T) {
	setDuration(t, &streamWriteTimeout, 200*time.Millisecond)
	hub := stream.NewHub()
	ts := listen(t, hub)
	conn, err := net.Dial("tcp", strings.TrimPrefix(ts.URL, "http://"))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	fmt.Fprint(conn, "GET /events HTTP/1.1\r\nHost: x\r\n\r\n") // and never read
	waitFor(t, "the stream to subscribe", func() bool { c, _, _, _ := hub.Stats(); return c == 1 })

	big := strings.Repeat("a", 64<<10)
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if c, _, _, _ := hub.Stats(); c == 0 {
			return
		}
		hub.Emit(map[string]any{"kind": "message", "text": big})
	}
	t.Fatal("a stream that stopped reading was never disconnected")
}

func waitFor(t *testing.T, what string, ok func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for !ok() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", what)
		}
		time.Sleep(5 * time.Millisecond)
	}
}
