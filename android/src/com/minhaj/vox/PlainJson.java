package com.minhaj.vox;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A small JSON reader and writer for the relay sync, in plain Java: the answers of the relay, the notes and the
 * profile it is sent, and the profile snapshot kept next to the notes. It exists because org.json is not available
 * to the off-device tests (the android.jar it comes from only has stubs), and with it RelayClient can be tested
 * against a real HTTP server.
 *
 * Values: null, Boolean, Long (a number written without fraction or exponent, when it fits), Double (any other
 * number), String, List (an ArrayList) and Map (a LinkedHashMap that keeps the order of the members). Reading is
 * strict (RFC 8259: no comments, no trailing commas, no single quotes, no raw control characters in strings) and
 * stops at {@link #MAX_DEPTH} levels of nesting, so a hostile answer cannot overflow the stack. Anything that is
 * not JSON is an IllegalArgumentException and never another exception.
 */
final class PlainJson {
    private PlainJson() { }

    /** Levels of arrays and objects inside each other that parse accepts. */
    static final int MAX_DEPTH = 64;

    // ------------------------------------------------------------------ reading

    /** The value in {@code text}, which must be exactly one JSON value. */
    static Object parse(String text) {
        if (text == null) throw new IllegalArgumentException("no JSON");
        Reader r = new Reader(text);
        r.skipSpace();
        Object v = r.value(0);
        r.skipSpace();
        if (r.pos != text.length()) throw r.fail("text after the value");
        return v;
    }

    private static final class Reader {
        private final String s;
        private int pos;

        Reader(String s) {
            this.s = s;
        }

        IllegalArgumentException fail(String why) {
            return new IllegalArgumentException("not JSON: " + why + " at " + pos);
        }

        void skipSpace() {
            while (pos < s.length()) {
                char c = s.charAt(pos);
                if (c == ' ' || c == '\t' || c == '\n' || c == '\r') pos++;
                else break;
            }
        }

        Object value(int depth) {
            if (pos >= s.length()) throw fail("the text ends early");
            char c = s.charAt(pos);
            switch (c) {
                case '{': return object(depth);
                case '[': return array(depth);
                case '"': return string();
                case 't': return word("true", Boolean.TRUE);
                case 'f': return word("false", Boolean.FALSE);
                case 'n': return word("null", null);
                default:
                    if (c == '-' || (c >= '0' && c <= '9')) return number();
                    throw fail("unexpected character");
            }
        }

        Object word(String w, Object value) {
            if (!s.startsWith(w, pos)) throw fail("unexpected word");
            pos += w.length();
            return value;
        }

        Map<String, Object> object(int depth) {
            if (depth >= MAX_DEPTH) throw fail("nested too deep");
            Map<String, Object> out = new LinkedHashMap<>();
            pos++;   // {
            skipSpace();
            if (pos < s.length() && s.charAt(pos) == '}') {
                pos++;
                return out;
            }
            while (true) {
                skipSpace();
                if (pos >= s.length() || s.charAt(pos) != '"') throw fail("a member name in quotes was expected");
                String key = string();
                skipSpace();
                if (pos >= s.length() || s.charAt(pos) != ':') throw fail("a colon was expected");
                pos++;
                skipSpace();
                out.put(key, value(depth + 1));
                skipSpace();
                if (pos >= s.length()) throw fail("the object is not closed");
                char c = s.charAt(pos++);
                if (c == '}') return out;
                if (c != ',') throw fail("a comma or } was expected");
            }
        }

        List<Object> array(int depth) {
            if (depth >= MAX_DEPTH) throw fail("nested too deep");
            List<Object> out = new ArrayList<>();
            pos++;   // [
            skipSpace();
            if (pos < s.length() && s.charAt(pos) == ']') {
                pos++;
                return out;
            }
            while (true) {
                skipSpace();
                out.add(value(depth + 1));
                skipSpace();
                if (pos >= s.length()) throw fail("the array is not closed");
                char c = s.charAt(pos++);
                if (c == ']') return out;
                if (c != ',') throw fail("a comma or ] was expected");
            }
        }

        String string() {
            pos++;   // the opening quote
            StringBuilder sb = new StringBuilder();
            while (true) {
                if (pos >= s.length()) throw fail("the string is not closed");
                char c = s.charAt(pos++);
                if (c == '"') return sb.toString();
                if (c < 0x20) throw fail("a raw control character in a string");
                if (c != '\\') {
                    sb.append(c);
                    continue;
                }
                if (pos >= s.length()) throw fail("the string is not closed");
                char e = s.charAt(pos++);
                switch (e) {
                    case '"': sb.append('"'); break;
                    case '\\': sb.append('\\'); break;
                    case '/': sb.append('/'); break;
                    case 'b': sb.append('\b'); break;
                    case 'f': sb.append('\f'); break;
                    case 'n': sb.append('\n'); break;
                    case 'r': sb.append('\r'); break;
                    case 't': sb.append('\t'); break;
                    case 'u':
                        if (pos + 4 > s.length()) throw fail("a short \\u escape");
                        int code = 0;
                        for (int i = 0; i < 4; i++) {
                            int d = Character.digit(s.charAt(pos + i), 16);
                            if (d < 0) throw fail("a bad \\u escape");
                            code = code * 16 + d;
                        }
                        pos += 4;
                        sb.append((char) code);
                        break;
                    default:
                        throw fail("a bad escape");
                }
            }
        }

        Object number() {
            int start = pos;
            if (s.charAt(pos) == '-') pos++;
            if (pos >= s.length()) throw fail("a number was expected");
            if (s.charAt(pos) == '0') {
                pos++;
            } else if (s.charAt(pos) >= '1' && s.charAt(pos) <= '9') {
                digits();
            } else {
                throw fail("a number was expected");
            }
            boolean whole = true;
            if (pos < s.length() && s.charAt(pos) == '.') {
                whole = false;
                pos++;
                if (digits() == 0) throw fail("digits were expected after the dot");
            }
            if (pos < s.length() && (s.charAt(pos) == 'e' || s.charAt(pos) == 'E')) {
                whole = false;
                pos++;
                if (pos < s.length() && (s.charAt(pos) == '+' || s.charAt(pos) == '-')) pos++;
                if (digits() == 0) throw fail("digits were expected in the exponent");
            }
            String text = s.substring(start, pos);
            if (whole) {
                try {
                    return Long.valueOf(text);
                } catch (NumberFormatException tooBig) {
                    // a whole number past 64 bits: fall through to a Double
                }
            }
            return Double.valueOf(text);
        }

        int digits() {
            int n = 0;
            while (pos < s.length() && s.charAt(pos) >= '0' && s.charAt(pos) <= '9') {
                pos++;
                n++;
            }
            return n;
        }
    }

    // ------------------------------------------------------------------ writing

    /**
     * The JSON text of a value: null, Boolean, Number, String, List (of these) or Map with String keys (to these).
     * Non-ASCII text is written as it is (the caller encodes the result as UTF-8); a Double is written in the form
     * {@code Double.toString} gives, which is valid JSON and reads back as the same number. An IllegalArgumentException
     * for anything else, and for a NaN or infinite number, which JSON cannot hold.
     */
    static String stringify(Object value) {
        StringBuilder sb = new StringBuilder();
        write(sb, value, 0);
        return sb.toString();
    }

    private static void write(StringBuilder sb, Object v, int depth) {
        if (depth > MAX_DEPTH) throw new IllegalArgumentException("nested too deep");
        if (v == null) {
            sb.append("null");
        } else if (v instanceof Boolean) {
            sb.append(((Boolean) v).booleanValue() ? "true" : "false");
        } else if (v instanceof Double) {
            double d = ((Double) v).doubleValue();
            if (Double.isNaN(d) || Double.isInfinite(d)) throw new IllegalArgumentException("JSON has no NaN or infinity");
            sb.append(Double.toString(d));
        } else if (v instanceof Long || v instanceof Integer || v instanceof Short || v instanceof Byte) {
            sb.append(((Number) v).longValue());
        } else if (v instanceof String) {
            quote(sb, (String) v);
        } else if (v instanceof List) {
            sb.append('[');
            boolean first = true;
            for (Object item : (List<?>) v) {
                if (!first) sb.append(',');
                first = false;
                write(sb, item, depth + 1);
            }
            sb.append(']');
        } else if (v instanceof Map) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                if (!(e.getKey() instanceof String)) throw new IllegalArgumentException("a JSON object needs text keys");
                if (!first) sb.append(',');
                first = false;
                quote(sb, (String) e.getKey());
                sb.append(':');
                write(sb, e.getValue(), depth + 1);
            }
            sb.append('}');
        } else {
            throw new IllegalArgumentException("not a JSON value: " + v.getClass().getSimpleName());
        }
    }

    private static void quote(StringBuilder sb, String s) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                case '\b': sb.append("\\b"); break;
                case '\f': sb.append("\\f"); break;
                default:
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        sb.append('"');
    }
}
