"""User-level settings — *choices*, never credentials.

`library.toml` is per-KB and tracked in git; provider and tier preferences are neither. This file
sits beside `~/.klode/registry.toml`, which already established the user-level, untracked location.

**Precedence: argument → environment → file → built-in default.** That chain is only implementable
if an omitted argument is distinguishable from an explicitly-passed one, which is why every
settings-backed CLI flag must default to `None` rather than to its value. `--tier` defaulting to
`"auto"` made `klode ingest x` and `klode ingest x --tier auto` produce identical namespaces, so
the resolver could not tell a deliberate choice from silence. `resolve()` records which source won
for exactly this reason: a split configuration surface nobody can audit is worse than one file.

**What stays out, and why:**

- **API keys** — environment only (`ANTHROPIC_API_KEY`). A key in a file is a key in a backup.

**What was moved IN, and why the earlier reasoning was wrong.** `ingest.docling_url` was env-only
on the grounds that it names an internal host. That conflated *topology* with a *credential*. A
docling-serve URL is not a secret: anyone who can route to the address can use the service, so URL
obscurity protects nothing — the real control is where the service binds (bind it to a private
interface, not to `0.0.0.0`). Meanwhile the env-only rule imposed a genuine cost: the one backend
that fixes multi-column reading order was unreachable unless you remembered to export a variable,
which is how a capability goes unused. So it is configurable here, and the *credential* line is
drawn harder rather than more vaguely — `tests/test_settings.py::SecretsStayOut` asserts no
key/token/secret/password may ever become a setting.

A URL in this file is still topology in a backup. That is a real cost, accepted knowingly: it is
not a credential, it grants nothing on its own, and `KLODE_DOCLING_URL` still overrides it for
anyone who prefers to keep it ephemeral.

**Scope, stated honestly:** this configures a judge *model* and ingest defaults. It is not a
"provider" abstraction — there is exactly one judge transport (Anthropic). Calling it provider
configuration would promise a contract that does not exist.

**The judge settings are consumed only under `klode review --live-judge`.** That split is
deliberate. A persistent config file is an adequate consent surface for CHOOSING a model; it is not
adequate consent for BILLED network calls, because `ANTHROPIC_API_KEY` is commonly ambient for
other tools and this command advertises a stub judge. Without the flag klode makes no network call
whatever these values say; with it, both a model and a key are required and the cost is stated
before the call.

`judge.hurdle` IS consumed. It was in the same inert state — declared, printed by `klode settings`,
and read by nothing, while the review service hardcoded `60`. A setting that cannot change
behaviour is worse than no setting: it invites a change and then ignores it silently.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

__all__ = ["Setting", "Settings", "SPEC", "load", "resolve", "settings_path", "DEFAULTS"]
#            DEFAULTS is a read-only view of SPEC, not a second source of truth: mutating it
#            changed nothing about resolution, which is worse than not exporting it at all.

ARG, ENV, FILE, DEFAULT, UNSET = "argument", "environment", "file", "default", "unset"


@dataclass(frozen=True)
class Spec:
    """One setting: where it may come from, and how to read it."""
    section: str
    key: str
    env: str | None
    default: object
    kind: type
    help: str
    choices: tuple = ()          # allowed values, when the domain is closed
    lo: object = None            # inclusive numeric bounds
    hi: object = None
    prefixes: tuple = ()         # allowed string prefixes, when the shape is constrained
    is_url: bool = False         # validate structurally as a service endpoint


SPEC: tuple[Spec, ...] = (
    Spec("judge", "model", "KLODE_JUDGE_MODEL", None, str,
         "model id for the rubric judge, used by `klode review --live-judge`. Nothing happens "
         "without that flag: a config file is consent to CHOOSE a model, not to spend money. No "
         "default on purpose (self-enhancement bias: it must differ from whatever produced the "
         "draft)"),
    Spec("judge", "permutations", "KLODE_JUDGE_PERMUTATIONS", 2, int,
         "how many opposed level orders to average over, against position bias. 1 (one forward "
         "pass, explicitly undebiased) or an even number to 16 — an odd count presents one order "
         "more often and keeps the bias the average exists to cancel. Each costs an API call "
         "under --live-judge: 1 + permutations per criterion",
         choices=(1, 2, 4, 6, 8, 10, 12, 14, 16)),
    Spec("judge", "hurdle", "KLODE_JUDGE_HURDLE", 60, int,
         "Go/Recycle threshold, 0..100 — the mean criterion percentage a draft must reach",
         lo=0, hi=100),
    Spec("ingest", "tier", "KLODE_INGEST_TIER", "auto", str,
         "default PDF extraction tier. `marker` is selectable but is NOT in the `auto` ladder — a "
         "backend earns a ladder slot by measuring better (eval/extract_bakeoff.py), not by being "
         "available",
         choices=("auto", "pdftotext", "xberg", "docling", "marker")),
    Spec("ingest", "verify", "KLODE_INGEST_VERIFY", True, bool,
         "check extraction integrity before promoting to the shelf"),
    Spec("ingest", "docling_url", "KLODE_DOCLING_URL", None, str,
         "docling-serve endpoint, e.g. http://<host>:15001 — the remote layout backend. Bind the "
         "service to a private interface; this URL is not a secret and must not be treated as one",
         prefixes=("http://", "https://"), is_url=True),
    Spec("ingest", "marker_url", "KLODE_MARKER_URL", None, str,
         "marker_server endpoint, e.g. http://<host>:15002 — remote-only, there is no local marker",
         prefixes=("http://", "https://"), is_url=True),
    Spec("ingest", "marker_mode", "KLODE_MARKER_MODE", "fast", str,
         "marker conversion mode. `fast` uses a lightweight layout detector and block-OCRs only "
         "garbled content; `balanced` runs the VLM layout model over every page. `fast` REDUCES "
         "VLM use but does not eliminate it — where the VLM inference server cannot start, an "
         "affected document stalls while it fails and then completes by fallback, so the symptom "
         "is latency and not an error",
         choices=("fast", "balanced")),
)

DEFAULTS = MappingProxyType({f"{s.section}.{s.key}": s.default for s in SPEC})


@dataclass(frozen=True)
class Setting:
    name: str
    value: object
    source: str          # ARG | ENV | FILE | DEFAULT | UNSET

    def __str__(self) -> str:
        shown = "(unset)" if self.value is None else repr(self.value)
        return f"{self.name:<24} {shown:<24} {self.source}"


class Settings(dict):
    """`name -> Setting`, so the winning value AND its origin are both available."""

    def value(self, name: str):
        """The winning value. A name that is not a setting raises — `r.value("ingest.verfy")`
        silently returned None, indistinguishable from a setting deliberately left unset, so a
        typo at a CALL SITE quietly disabled whatever that value was guarding."""
        try:
            return self[name].value
        except KeyError:
            raise KeyError(f"{name!r} is not a setting; known: {sorted(DEFAULTS)}") from None

    def source(self, name: str) -> str:
        try:
            return self[name].source
        except KeyError:
            raise KeyError(f"{name!r} is not a setting; known: {sorted(DEFAULTS)}") from None


def settings_path(explicit: str | Path | None = None, *, home: str | Path | None = None) -> Path:
    """Where settings live.

    `explicit` — a caller-supplied path, used verbatim (with `~` expanded). `home` — override the
    home directory; the file is then `<home>/.klode/settings.toml`. Purely a path computation: it
    does not touch the filesystem and never raises.
    """
    base = Path(home) if home is not None else Path.home()
    return Path(explicit).expanduser() if explicit else base / ".klode" / "settings.toml"


def load(explicit: str | Path | None = None, *, home: str | Path | None = None) -> dict:
    """Parse the settings file into `{"section.key": value}`. A missing file is not an error — the
    same posture as the registry. A malformed one IS, because silently ignoring a file the user
    wrote is how a setting appears to have no effect."""
    path = settings_path(explicit, home=home)
    if not path.exists():
        if explicit is not None:
            # asked for BY NAME. Silently returning {} meant a typo'd --settings path looked like
            # a working run with every value at its default.
            raise ValueError(f"{path}: no such settings file")
        return {}                                    # the implicit default may legitimately be absent
    if not path.is_file():
        raise ValueError(f"{path}: not a regular file")
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path}: invalid TOML — {e}") from e
    except OSError as e:
        raise ValueError(f"{path}: cannot read — {e}") from e

    known = {(s.section, s.key) for s in SPEC}
    known_sections = {s.section for s in SPEC}
    out: dict = {}
    for section, body in raw.items():
        if section not in known_sections:
            # an EMPTY typo'd table (`[ingset]`) has no keys to iterate, so the unknown-key check
            # never fired and the section was silently dropped
            raise ValueError(f"{path}: unknown section [{section}] — known: "
                             f"{sorted(known_sections)}")
        if not isinstance(body, dict):
            raise ValueError(f"{path}: [{section}] must be a table, got {type(body).__name__}")
        for key, val in body.items():
            if (section, key) not in known:
                # an unknown key is nearly always a typo, and silently dropping it is how a
                # setting "does nothing" with no explanation
                raise ValueError(f"{path}: unknown setting [{section}].{key} — known keys: "
                                 + ", ".join(f"{s.section}.{s.key}" for s in SPEC))
            out[f"{section}.{key}"] = val
    return out


def _from_env(spec: Spec):
    if not spec.env:
        return None, False
    raw = os.environ.get(spec.env)
    if raw is None:
        return None, False
    if raw == "":
        # An OPTIONAL setting (no built-in default) has "absent" as a real state, and `FOO=` is the
        # ordinary way to express it — refusing that broke `KLODE_MARKER_URL=` as a way to disable
        # an endpoint, and because resolution visits every setting, it broke unrelated commands
        # too. A setting WITH a default is different: blanking it is ambiguous, and the classic
        # deployment bug `FOO=$TYPO` must not fall through silently.
        if spec.default is None:
            return None, False
        raise ValueError(f"{spec.env} is set but empty — unset it with `env -u {spec.env}` if you "
                         f"meant no override, or give it one of {spec.choices or 'its valid values'}")
    return _from_env_value(spec, raw)


def _from_env_value(spec: Spec, raw: str):
    if spec.kind is bool:
        low = raw.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True, True
        if low in ("0", "false", "no", "off"):
            return False, True
        raise ValueError(f"{spec.env}={raw!r} is not a boolean (use true/false)")
    if spec.kind is int:
        try:
            return int(raw), True
        except ValueError:
            raise ValueError(f"{spec.env}={raw!r} is not an integer")
    return raw, True


def _validate(spec: Spec, value, where: str):
    """Domain validation, applied to EVERY source. Type-checking alone accepted tier="bogus",
    hurdle=999, permutations=0, and an empty model — values that pass as the right type and then
    fail much later, or silently do the wrong thing."""
    if spec.choices and value not in spec.choices:
        raise ValueError(f"{where}: [{spec.section}].{spec.key} must be one of "
                         f"{spec.choices}, got {value!r}")
    if spec.lo is not None and value < spec.lo:
        raise ValueError(f"{where}: [{spec.section}].{spec.key} must be >= {spec.lo}, got {value}")
    if spec.hi is not None and value > spec.hi:
        raise ValueError(f"{where}: [{spec.section}].{spec.key} must be <= {spec.hi}, got {value}")
    if spec.kind is str and not spec.choices and not str(value).strip():
        raise ValueError(f"{where}: [{spec.section}].{spec.key} must not be empty")
    if spec.is_url:
        _validate_url(spec, str(value), where)
    return value


def _validate_url(spec: Spec, value: str, where: str) -> None:
    """A service endpoint, structurally checked — not a prefix match.

    A prefix check accepted `http://user:password@host`, which puts a CREDENTIAL in
    `settings.toml` and straight into every backup, breaking the one rule this module is strictest
    about. It also accepted `http://` (no host at all) and `https://h#f`, values that can only fail
    later and be blamed on the backend.

    Plain `http://` is allowed only to a PRIVATE destination — loopback, RFC1918, CGNAT/tailnet
    (100.64/10), link-local, or a `.local`/`.internal` name. klode uploads whole PDFs to these
    endpoints, so cleartext to a public host exposes the document to anyone on the path, and
    "bind it to a private interface" is advice about the server, not about the wire. Where the
    destination IS private, TLS is not the control and requiring it would make the documented
    deployment impossible — so that case stays allowed, deliberately.
    """
    from urllib.parse import urlsplit
    label = f"{where}: [{spec.section}].{spec.key}"
    if not value.lower().startswith(spec.prefixes):
        raise ValueError(f"{label} must start with one of {spec.prefixes}, got {value!r}")
    try:
        u = urlsplit(value)
    except ValueError as e:
        raise ValueError(f"{label} is not a valid URL ({e})") from e
    if u.username or u.password:
        raise ValueError(
            f"{label} must not carry credentials. A URL with userinfo puts a secret in a settings "
            "file and therefore in every backup — credentials are environment-only, never settings.")
    if u.query or u.fragment:
        raise ValueError(f"{label} must be a bare endpoint: no query string or fragment "
                         "(a query is another place a token hides), got {value!r}".format(value=value))
    if not u.hostname:
        raise ValueError(f"{label} has no host, got {value!r}")
    try:
        u.port                      # raises ValueError on a malformed or out-of-range port
    except ValueError as e:
        raise ValueError(f"{label} has an invalid port ({e})") from e
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value):
        # 0x7f is DEL — a control character the `< 0x20` test misses, and it was accepted in a
        # hostname
        raise ValueError(f"{label} contains whitespace or control characters, got {value!r}")
    if u.scheme.lower() == "http" and not _is_private_host(u.hostname) \
            and not _insecure_http_allowed():
        raise ValueError(
            f"{label} uses plaintext http to a host that may be public ({u.hostname}). klode "
            "uploads whole documents to this endpoint. Use https, point it at an address that "
            "cannot be public (loopback, RFC1918, tailnet 100.64/10, or a single-label name like "
            "`docling`), or set [ingest].allow_insecure_http = true to accept the risk knowingly.")


def _insecure_http_allowed() -> bool:
    raw = os.environ.get("KLODE_ALLOW_INSECURE_HTTP", "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _numeric_host(h: str):
    """The address a host SPELLS, for any spelling a resolver accepts — or None when it names
    nothing numeric.

    `ipaddress` parses only the canonical forms, and the single-label rule below reads anything
    without a dot as a container name. A bare integer has no dot, so `http://134744072` took that
    branch and was classified private — while resolving, in fact, to 8.8.8.8. Same for the hex
    literal `0x08080808`. Whole documents were uploadable in cleartext to a public address with no
    opt-in, past the one guard that exists to stop it.

    Non-canonical DOTTED forms (`010.010.010.010`, where a resolver reads leading zeros as octal
    but Python refuses them) deliberately return None: they fall through to the dotted branch and
    are treated as a public name. That is the fail-closed reading of an ambiguous spelling.
    """
    import ipaddress
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        pass
    try:
        n = int(h, 0) if h[:2] in ("0x", "0o", "0b") else int(h)
    except ValueError:
        return None
    if 0 <= n <= 0xFFFFFFFF:
        return ipaddress.IPv4Address(n)
    if 0 <= n < 2 ** 128:
        return ipaddress.IPv6Address(n)
    return None


def _is_private_ip(ip) -> bool:
    import ipaddress
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local
                # 100.64/10 is CGNAT shared address space — where tailscale/headscale live. Python
                # does not class it private, and it is exactly the documented deployment.
                or ip in ipaddress.ip_network("100.64.0.0/10")
                or (ip.version == 6 and ip in ipaddress.ip_network("fd00::/8")))


def _is_private_host(host: str | None) -> bool:
    """Is this destination on a network where TLS is not the meaningful control?"""
    if not host:
        return False
    h = host.strip("[]").lower()
    # RFC 2606 / RFC 6761 reserved names can never resolve to a real public host, so cleartext to
    # one leaks nothing. `.test` in particular is what a test suite is supposed to use.
    if h == "localhost" or h.endswith((".local", ".internal", ".lan", ".home.arpa",
                                       ".test", ".example", ".invalid", ".localhost")):
        return True
    # Classify by ADDRESS before falling back to any name rule: a host that spells an address is an
    # address, whatever spelling it wears. This must precede the single-label branch below, which
    # is what the integer spellings were escaping through.
    ip = _numeric_host(h)
    if ip is not None:
        return _is_private_ip(ip)
    # A SINGLE-LABEL name (`docling`, `docling-serve`) has no public DNS answer — it can only be
    # resolved by a container network, a hosts file, or a local search domain. Rejecting these
    # broke the ordinary Docker and Kubernetes deployment, which was the point of the setting.
    # Note the limit, stated rather than hidden: a lexical rule cannot prove where a name RESOLVES;
    # `KLODE_ALLOW_INSECURE_HTTP` exists for the cases it cannot classify.
    if "." not in h:
        return True
    return False                                      # a public DNS name


def _coerce(spec: Spec, value, where: str):
    if spec.kind is bool and not isinstance(value, bool):
        raise ValueError(f"{where}: [{spec.section}].{spec.key} must be a boolean, "
                         f"got {type(value).__name__}")
    if spec.kind is int and (isinstance(value, bool) or not isinstance(value, int)):
        raise ValueError(f"{where}: [{spec.section}].{spec.key} must be an integer, "
                         f"got {type(value).__name__}")
    if spec.kind is str and not isinstance(value, str):
        raise ValueError(f"{where}: [{spec.section}].{spec.key} must be a string, "
                         f"got {type(value).__name__}")
    return _validate(spec, value, where)


def lint(explicit=None, *, home=None) -> list[str]:
    """Validate EVERY value in the settings file, including ones an override shadows.

    Separate from `resolve` on purpose. Validating the whole file during ordinary resolution meant
    an obsolete `[ingest]` entry — correctly overridden and never used — aborted unrelated commands
    like `review`. That is a diagnostic, not a runtime failure, so it gets its own mode:
    `klode settings --lint`. A shadowed-but-broken value is still worth knowing about, because it
    becomes live the moment the override is removed.
    """
    problems: list[str] = []
    try:
        fv = load(explicit, home=home)
    except ValueError as e:
        return [str(e)]
    by_name = {f"{sp.section}.{sp.key}": sp for sp in SPEC}
    where = str(settings_path(explicit, home=home))
    for name, val in sorted(fv.items()):
        try:
            _coerce(by_name[name], val, where)
        except ValueError as e:
            problems.append(str(e))
    return problems


def resolve(args=None, *, file_values: dict | None = None, explicit=None, home=None) -> Settings:
    """Apply the precedence chain and record which source won for every setting.

    `args` is an argparse namespace (or any object with attributes). A settings-backed flag MUST
    default to `None` there, or an omitted flag is indistinguishable from an explicit one and the
    argument level of this chain silently swallows the other three.
    """
    if file_values is None:
        fv = load(explicit, home=home)
    else:
        # An injected mapping must face the SAME unknown-key check as a file on disk, or the
        # documented guarantee ("unknown keys are rejected") holds only for one of two paths.
        known = {f"{s.section}.{s.key}" for s in SPEC}
        unknown = sorted(set(file_values) - known)
        if unknown:
            raise ValueError(f"unknown setting(s) {unknown} — known: {sorted(known)}")
        fv = file_values
    out = Settings()
    for spec in SPEC:
        name = f"{spec.section}.{spec.key}"
        attr = f"{spec.section}_{spec.key}"

        arg = getattr(args, attr, None) if args is not None else None
        if arg is None and args is not None:
            arg = getattr(args, spec.key, None)        # allow a bare flag name too (--tier)
        if arg is not None:
            out[name] = Setting(name, _coerce(spec, arg, "argument"), ARG)
            continue

        env_val, found = _from_env(spec)
        if found:
            out[name] = Setting(name, _validate(spec, env_val, spec.env or "environment"), ENV)
            continue

        if name in fv:
            out[name] = Setting(name, _coerce(spec, fv[name], str(settings_path(explicit, home=home))),
                                FILE)
            continue

        out[name] = Setting(name, spec.default, DEFAULT if spec.default is not None else UNSET)
    return out
