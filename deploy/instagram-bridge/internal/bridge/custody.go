package bridge

// Bridge-owned attachment custody: inbound media bytes are copied to a
// bridge-local volume and referenced by an opaque immutable key; outbound
// uploads land here first through PUT /v1/attachments/{key}. A raw expiring
// URL or an app-local path is never a durable reference — the envelope's
// `attachments[]` and `message.media[].fetch_token` name custody keys only.
//
// Layout: `<dir>/<key>.bin` (the bytes) + `<dir>/<key>.json` (the record:
// sha256, size, media_type, file_name, pins). Writes are fsynced temp +
// rename, like the session store's rule.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sync"
	"time"
)

// MaxAttachmentBytes bounds one custody object (32 MiB covers the media
// send route's own bound).
const MaxAttachmentBytes = 32 << 20

var custodyKeyRE = regexp.MustCompile(`^[A-Za-z0-9._:-]{1,128}$`)

// CustodyRecord is the metadata beside each object.
type CustodyRecord struct {
	Key       string   `json:"key"`
	SHA256    string   `json:"sha256"`
	Size      int64    `json:"size"`
	MediaType string   `json:"media_type"`
	FileName  string   `json:"file_name,omitempty"`
	Pins      []string `json:"pins"`
	CreatedMS int64    `json:"created_ms"`
}

// Custody is the store itself.
type Custody struct {
	dir string
	mu  sync.Mutex // serializes put-vs-put for one key
}

// NewCustody creates the directory (0700) and binds the store.
func NewCustody(dir string) (*Custody, error) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	return &Custody{dir: dir}, nil
}

// AttachmentKey mints a deterministic custody key for an event's i-th
// attachment: replaying the same event names the same object.
func AttachmentKey(eventID string, i int) string {
	sum := sha256.Sum256([]byte(eventID))
	return fmt.Sprintf("att-%s-%d", hex.EncodeToString(sum[:])[:16], i)
}

// ValidKey reports whether a caller-supplied key is safe to store under.
func ValidKey(key string) bool { return custodyKeyRE.MatchString(key) }

// ErrKeyConflict is a PUT reusing a key for different bytes.
var ErrKeyConflict = errors.New("attachment key already held for different bytes")

// Put stores bytes under key, idempotent for identical content. `pin`
// attaches a retention owner (a work/action id or an event id).
func (c *Custody) Put(key string, r io.Reader, mediaType, fileName, pin string) (*CustodyRecord, error) {
	if !ValidKey(key) {
		return nil, fmt.Errorf("invalid attachment key")
	}
	data, err := io.ReadAll(io.LimitReader(r, MaxAttachmentBytes+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > MaxAttachmentBytes {
		return nil, fmt.Errorf("attachment exceeds %d bytes", MaxAttachmentBytes)
	}
	sum := sha256.Sum256(data)
	c.mu.Lock()
	defer c.mu.Unlock()
	if rec, err := c.load(key); err == nil {
		if rec.SHA256 != hex.EncodeToString(sum[:]) {
			return nil, ErrKeyConflict
		}
		if pin != "" && !contains(rec.Pins, pin) {
			rec.Pins = append(rec.Pins, pin)
			if err := c.writeMeta(rec); err != nil {
				return nil, err
			}
		}
		return rec, nil
	}
	rec := &CustodyRecord{
		Key: key, SHA256: hex.EncodeToString(sum[:]), Size: int64(len(data)),
		MediaType: mediaType, FileName: fileName,
		CreatedMS: time.Now().UnixMilli(),
	}
	if pin != "" {
		rec.Pins = []string{pin}
	}
	if err := c.writeFile(key, data); err != nil {
		return nil, err
	}
	if err := c.writeMeta(rec); err != nil {
		return nil, err
	}
	return rec, nil
}

// fetchError carries a LOW-CARDINALITY kind instead of the raw error: the
// upstream errors around a signed CDN GET can embed the full URL (bearer of
// its own access token), which never belongs in a log or a span.
type fetchError struct{ kind string }

func (e *fetchError) Error() string { return "media fetch: " + e.kind }

func fetchErr(kind string) error { return &fetchError{kind: kind} }

// errClass reduces any error to the closed vocabulary metrics, logs and
// spans may carry. Never returns the error's own text: upstream errors
// embed signed URLs, tokens and ids.
func errClass(err error) string {
	var fe *fetchError
	if errors.As(err, &fe) {
		return fe.kind
	}
	var ne net.Error
	if errors.As(err, &ne) && ne.Timeout() {
		return "timeout"
	}
	var de *net.DNSError
	if errors.As(err, &de) {
		return "dns"
	}
	switch {
	case errors.Is(err, context.DeadlineExceeded):
		return "timeout"
	case errors.Is(err, context.Canceled):
		return "canceled"
	}
	return "error"
}

// privateTarget reports whether an IP must never receive a signed-URL fetch:
// loopback, RFC-1918/link-local/ULA, multicast, unspecified. A CDN object is
// public by construction; a resolved private answer is a redirect/DNS attack
// or a misconfiguration, never a legitimate target.
func privateTarget(ip net.IP) bool {
	return ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() ||
		ip.IsLinkLocalMulticast() || ip.IsMulticast() || ip.IsUnspecified()
}

// NewFetchClient builds the policy client every signed-URL fetch uses:
//   - https ONLY (a signed CDN object never needs plaintext);
//   - EVERY dial resolves the host and refuses private targets — redirects
//     re-dial, so the check also binds every hop;
//   - 20s ceiling; a stalled CDN does not pin the drainer.
func NewFetchClient() *http.Client {
	dialer := &net.Dialer{Timeout: 10 * time.Second}
	transport := &http.Transport{
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			host, _, err := net.SplitHostPort(addr)
			if err != nil {
				return nil, fetchErr("address")
			}
			ips, err := net.DefaultResolver.LookupIP(ctx, "ip", host)
			if err != nil {
				return nil, fetchErr("dns")
			}
			for _, ip := range ips {
				if privateTarget(ip) {
					return nil, fetchErr("private_target")
				}
			}
			return dialer.DialContext(ctx, network, addr)
		},
	}
	return &http.Client{
		Timeout:   20 * time.Second,
		Transport: transport,
		CheckRedirect: func(req *http.Request, _ []*http.Request) error {
			if req.URL.Scheme != "https" {
				return fetchErr("scheme")
			}
			return nil
		},
	}
}

// FetchURL copies a remote object into custody under key. hc must be the
// policy client (NewFetchClient): the URL is a signed CDN object needing no
// session, and fetches may never reach private space. Failures come back as
// *fetchError — classify with errClass, never log the raw error (it can
// embed the signed URL).
func (c *Custody) FetchURL(ctx context.Context, hc *http.Client, rawURL, key, pin string) (*CustodyRecord, error) {
	u, err := url.Parse(rawURL)
	if err != nil || u.Scheme != "https" {
		return nil, fetchErr("scheme")
	}
	req, err := http.NewRequestWithContext(ctx, "GET", rawURL, nil)
	if err != nil {
		return nil, fetchErr("request")
	}
	resp, err := hc.Do(req)
	if err != nil {
		var fe *fetchError
		if errors.As(err, &fe) {
			return nil, err
		}
		return nil, fetchErr(errClass(err))
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		kind := "http_error"
		if resp.StatusCode >= 400 && resp.StatusCode < 500 {
			kind = "http_4xx"
		} else if resp.StatusCode >= 500 {
			kind = "http_5xx"
		}
		return nil, fetchErr(kind)
	}
	mediaType := resp.Header.Get("Content-Type")
	if mediaType == "" {
		mediaType = "application/octet-stream"
	}
	return c.Put(key, resp.Body, mediaType, "", pin)
}

// Get returns the record and opens the object. It is an error (nil, false)
// for an unknown key — never a fabricated empty file.
func (c *Custody) Get(key string) (*CustodyRecord, []byte, bool) {
	if !ValidKey(key) {
		return nil, nil, false
	}
	rec, err := c.load(key)
	if err != nil {
		return nil, nil, false
	}
	data, err := os.ReadFile(c.binPath(key))
	if err != nil {
		return nil, nil, false
	}
	return rec, data, true
}

// Stat reports the custody record when BOTH sides of key exist on disk and
// the stored bytes still hash to the recorded sha256 — the crash-recovery
// check the replay path uses: an object spooled before a crash is reused,
// never re-fetched through its (possibly expired) upstream URL. A partial
// or corrupt spool reports absent so the caller fetches as usual.
func (c *Custody) Stat(key string) (*CustodyRecord, bool) {
	rec, data, ok := c.Get(key)
	if !ok {
		return nil, false
	}
	sum := sha256.Sum256(data)
	if rec.SHA256 != hex.EncodeToString(sum[:]) {
		return nil, false
	}
	return rec, true
}

func (c *Custody) binPath(key string) string { return filepath.Join(c.dir, filepath.Base(key)+".bin") }
func (c *Custody) metaPath(key string) string {
	return filepath.Join(c.dir, filepath.Base(key)+".json")
}

func (c *Custody) load(key string) (*CustodyRecord, error) {
	raw, err := os.ReadFile(c.metaPath(key))
	if err != nil {
		return nil, err
	}
	var rec CustodyRecord
	if err := json.Unmarshal(raw, &rec); err != nil {
		return nil, err
	}
	return &rec, nil
}

func (c *Custody) writeFile(key string, data []byte) error {
	tmp, err := os.CreateTemp(c.dir, ".tmp-*.bin")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Chmod(tmp.Name(), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), c.binPath(key))
}

func (c *Custody) writeMeta(rec *CustodyRecord) error {
	raw, err := json.Marshal(rec)
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(c.dir, ".tmp-*.json")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(raw); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Chmod(tmp.Name(), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), c.metaPath(rec.Key))
}

func contains(list []string, v string) bool {
	for _, s := range list {
		if s == v {
			return true
		}
	}
	return false
}
