package main

// The dependency rule, as a test (design/instagram-adoption/16 §Layers, the
// "must not import" column). `go list -deps` gives each layer's TRANSITIVE
// production imports, so a forbidden package reached through a helper counts
// the same as one imported directly. Test-only imports are not production
// dependencies and are not counted.

import (
	"os/exec"
	"strings"
	"testing"
)

const module = "instagram-bridge"

type dep struct {
	path     string
	standard bool
}

func depsOf(t *testing.T, pkg string) []dep {
	t.Helper()
	out, err := exec.Command("go", "list", "-deps", "-f", "{{.ImportPath}} {{.Standard}}", pkg).CombinedOutput()
	if err != nil {
		// ERROR, not PASS: a rule that cannot be measured has not been met.
		t.Fatalf("go list -deps %s: %v\n%s", pkg, err, out)
	}
	var deps []dep
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		path, std, _ := strings.Cut(line, " ")
		if path == pkg {
			continue
		}
		deps = append(deps, dep{path: path, standard: std == "true"})
	}
	if len(deps) == 0 {
		t.Fatalf("go list -deps %s listed nothing; the measurement is broken", pkg)
	}
	return deps
}

func under(path, prefix string) bool { return path == prefix || strings.HasPrefix(path, prefix+"/") }

// rule is one row of 16's table: what a layer must not reach, transitively.
type rule struct {
	pkg string
	// stdlibOnly: every dependency is the standard library.
	stdlibOnly bool
	// forbidden import-path prefixes.
	forbidden []string
	// allowedInternal, when non-nil, is the complete list of this module's
	// packages the layer may reach.
	allowedInternal []string
}

var layers = []rule{
	{
		pkg:        module + "/internal/domain",
		stdlibOnly: true,
	},
	{
		pkg:             module + "/internal/app",
		forbidden:       []string{"net/http", "go.mau.fi/mautrix-meta", module + "/internal/transport", module + "/internal/gateway"},
		allowedInternal: []string{module + "/internal/domain"},
	},
	{
		pkg:       module + "/internal/gateway/instameow",
		forbidden: []string{module + "/internal/transport", module + "/cmd"},
	},
	{
		pkg: module + "/internal/transport/http",
		forbidden: []string{
			"go.mau.fi/mautrix-meta", module + "/internal/gateway", module + "/internal/session",
			module + "/cmd",
		},
	},
}

func TestLayersImportOnlyInward(t *testing.T) {
	for _, l := range layers {
		t.Run(strings.TrimPrefix(l.pkg, module+"/"), func(t *testing.T) {
			for _, d := range depsOf(t, l.pkg) {
				if l.stdlibOnly && !d.standard {
					t.Errorf("%s may import only the standard library; it reaches %s", l.pkg, d.path)
				}
				for _, f := range l.forbidden {
					if under(d.path, f) {
						t.Errorf("%s must not import %s; it reaches %s", l.pkg, f, d.path)
					}
				}
				if l.allowedInternal != nil && under(d.path, module) {
					ok := false
					for _, a := range l.allowedInternal {
						ok = ok || under(d.path, a)
					}
					if !ok {
						t.Errorf("%s may reach only %v of this module; it reaches %s", l.pkg, l.allowedInternal, d.path)
					}
				}
			}
		})
	}
}

// Positive control: the measurement can see a forbidden edge. The gateway
// really does reach instameow and the binary really does reach every layer —
// a `go list` that returned the wrong package, or nothing useful, would make
// every rule above pass vacuously.
func TestTheImportMeasurementSeesRealEdges(t *testing.T) {
	has := func(pkg, want string) bool {
		for _, d := range depsOf(t, pkg) {
			if under(d.path, want) {
				return true
			}
		}
		return false
	}
	if !has(module+"/internal/gateway/instameow", "go.mau.fi/mautrix-meta") {
		t.Error("the gateway does not appear to reach instameow; the measurement is blind")
	}
	for _, layer := range []string{"/internal/app", "/internal/transport/http", "/internal/gateway/instameow", "/internal/domain"} {
		if !has(module+"/cmd/instagram-bridge", module+layer) {
			t.Errorf("the binary does not reach %s; the measurement is blind", layer)
		}
	}
	if !has(module+"/internal/transport/http", "net/http") {
		t.Error("transport does not appear to reach net/http; standard packages are not being listed")
	}
}
