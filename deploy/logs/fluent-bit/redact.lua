-- Log redaction (P1-T9b, ADR-013; owner decisions Q-O5..Q-O7). Fluent Bit runs two functions
-- from this file on every journal entry, in this order (fluent-bit/filters.yaml):
--
--   shape()   keeps a few journald fields and names the source; everything else journald
--             attaches (command lines, capabilities, IDs) is dropped
--   redact()  Keycloak event lines are rebuilt from an allow-list; then every string is masked:
--             tokens and secrets, query strings, database values, e-mail addresses, mobile,
--             12-digit (Aadhaar-like) and ABHA numbers, user names in paths
--
-- A Lua error drops that one record (protected mode, the default): a line is stored redacted
-- or not at all. Fluent Bit counts such drops; Prometheus alerts on them (LogRedactionDrops).
-- Tests: scripts/check-logs.py (cases in deploy/logs/tests/redaction-cases.json).

-- ---- shape --------------------------------------------------------------------------------

local function str(v)
  if type(v) == "string" then return v end
  if v == nil then return nil end
  return tostring(v)
end

function shape(tag, ts, r)
  local out = {}
  local msg = r.MESSAGE
  if type(msg) == "table" then
    -- journald hands over non-text messages as byte arrays: keep them as text
    local ok, s = pcall(string.char, unpack(msg))
    msg = ok and s or ""
  end
  out.msg = str(msg) or ""
  out.host = str(r._HOSTNAME)
  out.priority = tonumber(r.PRIORITY)
  -- A container's line only when journald itself says it came from Docker: these "_" fields are
  -- set by journald and cannot be chosen by the process that logs. Any process on the host can
  -- add CONTAINER_NAME or COM_DOCKER_COMPOSE_SERVICE to its own entries; those stay host lines,
  -- so they can never pass for Keycloak's or Traefik's (the log alerts read only containers).
  if r.CONTAINER_NAME and r._TRANSPORT == "journal" and r._COMM == "dockerd" and r._UID == "0" then
    out.source = "container"
    -- The compose service (logging option labels: com.docker.compose.service)
    out.service = str(r.COM_DOCKER_COMPOSE_SERVICE) or str(r.CONTAINER_NAME)
    out.container = str(r.CONTAINER_NAME)
    out.image = str(r.IMAGE_NAME)
    if r.CONTAINER_PARTIAL_MESSAGE then out.partial = str(r.CONTAINER_PARTIAL_MESSAGE) end
  else
    out.source = "host"
    out.service = str(r.SYSLOG_IDENTIFIER) or str(r._COMM) or "unknown"
    out.unit = str(r._SYSTEMD_UNIT)
    out.pid = str(r._PID)
  end
  return 2, ts, out
end

-- ---- masks --------------------------------------------------------------------------------

local function ci(word)  -- a case-insensitive Lua pattern for a literal word
  local p = word:gsub("%a", function(c) return "[" .. c:lower() .. c:upper() .. "]" end)
  p = p:gsub("%-", "%%-")
  return p
end

local SECRET_KEYS = {
  "password", "passwd", "passphrase", "secret", "token", "api_key", "apikey", "api-key",
  "access_key", "secret_key", "private_key", "client_secret", "cipher-pass", "key-secret",
  "s3-key", "authorization", "otp", "auth_code",
}
local SECRET_PATTERNS = {}
for _, k in ipairs(SECRET_KEYS) do SECRET_PATTERNS[#SECRET_PATTERNS + 1] = { ci(k), k == "authorization" } end
local P_REQUIREPASS = ci("requirepass")
local P_BEARER = ci("bearer")
local P_BASIC = ci("basic")
local HEX64 = string.rep("%x", 64)

local function is_placeholder(v) return v:sub(1, 1) == "<" end
-- CARE's list-level user endpoints (not a user name): left as they are
local USER_ACTIONS = { getcurrentuser = true, set_preferences = true }

-- One maximal run of digits, judged by its length and what touches it. Not masked: digits that
-- are part of a word, a hex string, a decimal, a UUID, a path or a version (a letter, digit,
-- ".", "-", "_", "/" or "#" right before, or a letter, digit, "." + digit or "_" right after),
-- and JSON numbers (":" after a quote).
local function digit_run(s)
  return (s:gsub("()(%d+)()", function(a, d, b)
    local before = a > 1 and s:sub(a - 1, a - 1) or ""
    local after = s:sub(b, b)
    if before:match("[%w%.%-_/#]") or after:match("[%w_]") then return nil end
    if after == "." and s:sub(b + 1, b + 1):match("%d") then return nil end
    if before == ":" and a > 2 and s:sub(a - 2, a - 2) == '"' then return nil end
    local n, f = #d, d:sub(1, 1)
    if n == 10 and f:match("[6-9]") then return "<phone>" end
    if n == 11 and f == "0" and d:sub(2, 2):match("[6-9]") then return "<phone>" end
    if n == 12 and d:sub(1, 2) == "91" and d:sub(3, 3):match("[6-9]") then return "<phone>" end
    if n == 12 and f:match("[2-9]") then return "<aadhaar>" end
    if n == 14 then return "<abha>" end
    return nil
  end))
end

local function mask(s)
  if type(s) ~= "string" or s == "" then return s end
  -- Tokens
  s = s:gsub("eyJ[%w_%-]+%.[%w_%-]+%.[%w_%-]*", "<jwt>")
  s = s:gsub("(" .. P_BEARER .. "%s+)([%w%-%._~%+/=]+)", function(p, v)
    if is_placeholder(v) then return nil end
    return p .. "<redacted>"
  end)
  s = s:gsub("(" .. P_BASIC .. "%s+)" .. string.rep("[%w%+/]", 12) .. "[%w%+/]*=*", "%1<redacted>")
  -- Any other Authorization scheme (Token, Digest, ...)
  s = s:gsub("(" .. ci("authorization") .. "%s*:%s*[%w%-]+%s+)([^%s<][^%s]*)", "%1<redacted>")
  -- Secrets: key=value, "key": "value", --key value
  for _, kp in ipairs(SECRET_PATTERNS) do
    local k, is_auth = kp[1], kp[2]
    s = s:gsub("(" .. k .. "[\"']?%s*=%s*[\"']?)([^%s\"',;&}%]]+)", function(p, v)
      if is_placeholder(v) then return nil end
      return p .. "<redacted>"
    end)
    -- "key": "value", 'refresh_token': 'value' (JSON, Python reprs; any key containing it)
    s = s:gsub("([\"'][%w_%-]*" .. k .. "[%w_%-]*[\"']%s*:%s*[\"'])([^\"']*)([\"'])", function(p, v, q)
      if is_placeholder(v) then return nil end
      return p .. "<redacted>" .. q
    end)
    -- key: value (a word of its own; prose such as "token: expired" is masked too, on purpose)
    if not is_auth then  -- "Authorization: <scheme> <value>" has a rule of its own above
      s = s:gsub("(%f[%w_]" .. k .. "%s*:%s+)([^%s\"',;&}%]<]+)", "%1<redacted>")
    end
    s = s:gsub("(%-%-" .. k .. "%s+)([^%s%-][^%s]*)", function(p, v)
      if is_placeholder(v) then return nil end
      return p .. "<redacted>"
    end)
  end
  s = s:gsub("(" .. P_REQUIREPASS .. "%s+)\"[^\"]*\"", "%1<redacted>")
  s = s:gsub("(" .. P_REQUIREPASS .. "%s+)[^%s'\"<][^%s'\"]*", "%1<redacted>")
  s = s:gsub("#" .. HEX64, "#<sha256>")
  -- Query strings: everything after "?" in a path or address (searches, codes, setup links)
  s = s:gsub("(https?://[^%s\"'<>/?]+)%?[^%s\"'<>]+", "%1?<q>")
  s = s:gsub("(/[^%s\"'<>?]*)%?[^%s\"'<>]+", "%1?<q>")
  -- ...and a relative path without a "/" (users?email=...), when a key=value follows the "?"
  s = s:gsub("([%w_%.%-]+)%?[%w_%-]+=[^%s\"'<>]*", "%1?<q>")
  -- Database values echoed in errors (psycopg DETAIL lines, Postgres input errors)
  -- Everything after the marker, to the end of the line: values may hold unbalanced brackets
  -- or quotes (free-text addresses), so nothing is matched up to a closing one. The key's column
  -- list comes from an index definition and is always balanced.
  s = s:gsub("(Key %b())=%([^\n]*", "%1=(<x>)")
  s = s:gsub("(Failing row contains )%([^\n]*", "%1(<x>)")
  s = s:gsub("(invalid input syntax for [^:\"\n]+: )\"[^\n]*", "%1\"<x>\"")
  s = s:gsub("(invalid input value for [^:\"\n]+: )\"[^\n]*", "%1\"<x>\"")
  s = s:gsub("(out of range: )\"[^\n]*", "%1\"<x>\"")
  -- User names in API paths (they are e-mail local parts)
  s = s:gsub("(/api/v1/users/)([^/%s\"'?<]+)", function(p, seg)
    if USER_ACTIONS[seg] then return nil end
    return p .. "<user>"
  end)
  -- ABHA addresses, e-mail addresses (also URL-encoded)
  s = s:gsub("[%w%._]+@abdm%f[^%w]", "<abha>")
  s = s:gsub("[%w%._]+@sbx%f[^%w]", "<abha>")
  s = s:gsub("[%w%._%+%-]+%%40[%w%-][%w%.%-]*%.%a%a+", "<email>")
  s = s:gsub("[%w%._%%%+%-]+@[%w%-][%w%.%-]*%.%a%a+", "<email>")
  -- Numbers with separators: +91 mobiles, 5-5 / 3-3-4 mobiles, 4-4-4 Aadhaar, ABHA 2-4-4-4
  s = s:gsub("%+91[%s%-]?%d%d%d%d%d[%s%-]?%d%d%d%d%d%f[%D]", "<phone>")
  s = s:gsub("%f[%w]%d%d%-%d%d%d%d%-%d%d%d%d%-%d%d%d%d%f[%W]", "<abha>")
  s = s:gsub("%f[%w][2-9]%d%d%d[ %-]%d%d%d%d[ %-]%d%d%d%d%f[%W]", "<aadhaar>")
  s = s:gsub("%f[%w][6-9]%d%d%d%d[ %-]%d%d%d%d%d%f[%W]", "<phone>")
  s = s:gsub("%f[%w][6-9]%d%d[ %-]%d%d%d[ %-]%d%d%d%d%f[%W]", "<phone>")
  -- Plain digit runs: mobiles (10; 0 + 10; 91 + 10), 12-digit (Aadhaar-like), 14-digit (ABHA)
  s = digit_run(s)
  return s
end

-- ---- Keycloak events (jboss-logging listener; ADR-013) --------------------------------------
-- Line: <time> <LEVEL> [org.keycloak.events] (<thread>) type="LOGIN_ERROR", realmId="...", ...
-- Values are quoted, quotes inside escaped with "\" (Keycloak's sanitize, on by default).

local KC_KEEP = {
  type = true, operationType = true, realmId = true, realmName = true, clientId = true,
  userId = true, sessionId = true, ipAddress = true, error = true, resourceType = true,
  resourcePath = true, auth_method = true, auth_type = true, grant_type = true,
  client_auth_method = true, response_type = true, response_mode = true, scope = true,
  credential_type = true, reason = true, identity_provider = true, remember_me = true,
  authSessionParentId = true, authSessionTabId = true, redirect_uri = true, username = true,
  email = true, previous_email = true, updated_email = true, custom_required_action = true,
  restart_after_timeout = true, redirected_to_client = true, not_before = true,
  num_failures = true, client_jwt_kid = true, client_assertion_issuer = true,
  client_assertion_sub = true,
}
-- Kept as a marker only: the event had one
local KC_EMAIL = { email = true, previous_email = true, updated_email = true }
-- The typed user name means nothing (or may be a password typed into the wrong box) when no
-- account was found: dropped then. A real account's user name is kept as is (lock-out checks).
local KC_NO_ACCOUNT = { user_not_found = true, invalid_user = true, user_not_found_by_email = true }

-- Keycloak 26.8 (JBossLoggingEventListenerProvider, sanitize on, quote '"') writes every value
-- in quotes and escapes a quote inside as \" but never escapes a backslash. So: a backslash
-- escapes only a quote that follows it; anything else is literal. Typed text can then never
-- close its own quote. A typed trailing backslash swallows Keycloak's closing quote instead,
-- and the line goes out of step and fails below. Any unquoted value or repeated key fails too.
local function kc_parse(body)
  local pairs_, seen = {}, {}
  local i, n = 1, #body
  while i <= n do
    local k, j = body:match("^([%w_%.%-]+)=()", i)
    if not k or seen[k] or body:sub(j, j) ~= '"' then return nil end
    seen[k] = true
    local buf, p = {}, j + 1
    while true do
      if p > n then return nil end
      local c = body:sub(p, p)
      if c == "\\" and body:sub(p + 1, p + 1) == '"' then buf[#buf + 1] = '\\"'; p = p + 2
      elseif c == '"' then break
      else buf[#buf + 1] = c; p = p + 1 end
    end
    pairs_[#pairs_ + 1] = { k, table.concat(buf) }
    i = p + 1
    if i <= n then
      if body:sub(i, i + 1) ~= ", " then return nil end
      i = i + 2
    end
  end
  return pairs_
end

-- The fixed head Keycloak always writes before the details. Event fields (kc_event, kc_error,
-- client_ip, the account) come from here only, never from a detail.
local KC_HEAD_USER = { "type", "realmId", "realmName", "clientId", "userId", "?sessionId", "ipAddress", "?error" }
local KC_HEAD_ADMIN = { "operationType", "realmId", "realmName", "clientId", "userId", "ipAddress",
                        "resourceType", "resourcePath", "?error" }
local function kc_head_ok(kv)
  local head = kv[1] and (kv[1][1] == "type" and KC_HEAD_USER or kv[1][1] == "operationType" and KC_HEAD_ADMIN)
  if not head then return false end
  local i = 1
  for _, want in ipairs(head) do
    local optional = want:sub(1, 1) == "?"
    local name = optional and want:sub(2) or want
    if kv[i] and kv[i][1] == name then
      i = i + 1
    elseif not optional then
      return false
    end
  end
  return true
end

local function valid_ip(v)
  if type(v) ~= "string" or #v < 2 or #v > 45 then return nil end
  if v:match("^[%x%.:]+$") and v:match("[%.:]") then return v end
  return nil
end

local function keycloak_event(r)
  local msg = r.msg
  if type(msg) ~= "string" or not msg:find("[org.keycloak.events]", 1, true) then return end
  local start = msg:find('operationType="', 1, true) or msg:find('type="', 1, true)
  if not start then return end
  local prefix, body = msg:sub(1, start - 1), msg:sub(start)
  local kv = kc_parse(body)
  if not kv or not kc_head_ok(kv) then
    -- Unknown or tampered shape: everything from the user name to the end of the line goes
    -- (its value cannot be told apart from what follows), and nothing is taken as an event
    -- field. The rest is masked as usual.
    body = body:gsub("[%s,]*" .. ci("username") .. "%s*=.*$", ', username="<dropped>"')
    r.msg = prefix .. body
    r.kc_parse = "failed"
    return
  end
  local f = {}
  for _, p in ipairs(kv) do f[p[1]] = p[2] end
  local real_user = f.userId and f.userId ~= "" and f.userId ~= "null" and not KC_NO_ACCOUNT[f.error or ""]
  local out, dropped = {}, {}
  for _, p in ipairs(kv) do
    local k, v = p[1], p[2]
    if not KC_KEEP[k] then
      dropped[#dropped + 1] = k
    else
      if k == "username" then
        v = real_user and v or "<dropped>"
      elseif KC_EMAIL[k] then
        v = "<email>"
      elseif k == "redirect_uri" then
        v = mask((v:gsub("[?#].*$", "")))
      else
        v = mask(v)
      end
      out[#out + 1] = k .. '="' .. v .. '"'
    end
  end
  -- prefix is "<time> <LEVEL> [org.keycloak.events] (<thread>) ": masked like any line
  r.msg = mask(prefix) .. table.concat(out, ", ")
  r.kc_event = f.type or f.operationType
  r.kc_error = f.error
  r.client_ip = valid_ip(f.ipAddress)
  -- The account (an opaque id), so the lock-out alerts count accounts, not events
  if real_user then r.kc_user = f.userId end
  if #dropped > 0 then r.kc_dropped = table.concat(dropped, ",") end
  r.kc_done = true
end

function redact(tag, ts, r)
  if r.source == "container" and r.service == "keycloak" then keycloak_event(r) end
  if r.kc_done then
    r.kc_done = nil
  else
    r.msg = mask(r.msg)
  end
  -- Traefik's access log (one JSON object a line): the client's address, checked, the status
  -- and the router as fields of their own (the log alerts group by them)
  if r.source == "container" and r.service == "traefik" and type(r.msg) == "string" and r.msg:sub(1, 1) == "{" then
    r.client_ip = valid_ip(r.msg:match('"ClientHost":"([^"]*)"'))
    r.http_status = tonumber(r.msg:match('"DownstreamStatus":(%d+)'))
    r.router = r.msg:match('"RouterName":"([%w@%.%-_]*)"')
  end
  return 2, ts, r
end
