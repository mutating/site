#!/usr/bin/env bash
set -euo pipefail

site_headers() {
    curl --disable --http1.1 --silent --show-error --head --max-time 30 "$1" |
        tr -d '\r'
}

assert_redirect() {
    local source_url="$1"
    local expected_location="$2"
    local headers
    local location
    headers="$(site_headers "$source_url")"

    if ! grep -qE '^HTTP/[^ ]+ 308( |$)' <<< "$headers"; then
        printf 'Expected HTTP 308 from %s.\n' "$source_url" >&2
        exit 1
    fi
    if ! location="$(grep -i '^location: ' <<< "$headers" | cut -d ' ' -f2-)" ||
        [[ "$location" != "$expected_location" ]]; then
        printf 'Expected redirect from %s to %s, got %s.\n' \
            "$source_url" "$expected_location" "${location:-<missing>}" >&2
        exit 1
    fi
}

echo 'Checking public redirects, TLS and response headers over HTTP/1.1.'
canonical_blog='https://mutating.tech/blog/?probe=1'
assert_redirect 'http://mutating.tech/blog/?probe=1' "$canonical_blog"
assert_redirect 'http://www.mutating.tech/blog/?probe=1' "$canonical_blog"
assert_redirect 'https://www.mutating.tech/blog/?probe=1' "$canonical_blog"

# Check the server policy before site files exist. The post-upload step checks GET status.
root_headers="$(site_headers 'https://mutating.tech/')"
if ! grep -qiFx 'strict-transport-security: max-age=31536000; includeSubDomains' <<< "$root_headers"; then
    echo 'Unexpected Strict-Transport-Security header from https://mutating.tech/.' >&2
    exit 1
fi
if ! grep -qiFx 'cache-control: no-cache' <<< "$root_headers"; then
    echo 'Unexpected Cache-Control header from https://mutating.tech/.' >&2
    exit 1
fi
