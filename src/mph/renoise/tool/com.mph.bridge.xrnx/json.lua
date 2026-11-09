-- A small JSON encoder for result files (strings, numbers, booleans, arrays, tables).
local M = {}

local ESCAPES = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }

local function quote(text)
  local body = text:gsub('[%c"\\]', function(c)
    return ESCAPES[c] or string.format("\\u%04x", c:byte())
  end)
  return '"' .. body .. '"'
end

local function is_array(value)
  local count = 0
  for _ in pairs(value) do
    count = count + 1
  end
  return count == #value
end

function M.encode(value)
  local kind = type(value)
  if kind == "nil" then
    return "null"
  elseif kind == "boolean" then
    return tostring(value)
  elseif kind == "number" then
    return string.format("%.17g", value)
  elseif kind == "string" then
    return quote(value)
  elseif kind == "table" then
    local parts = {}
    if is_array(value) then
      for _, item in ipairs(value) do
        parts[#parts + 1] = M.encode(item)
      end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    local keys = {}
    for key in pairs(value) do
      keys[#keys + 1] = tostring(key)
    end
    table.sort(keys)
    for _, key in ipairs(keys) do
      parts[#parts + 1] = quote(key) .. ":" .. M.encode(value[key])
    end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  error("can't encode a " .. kind)
end

return M
