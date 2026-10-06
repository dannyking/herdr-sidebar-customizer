#!/usr/bin/env python3
"""Print a fictional coding-agent session in Claude Code's visual style.

Redraws on every resize and then waits, so a Herdr client shows it at its
final pane size. Every name, path and number below is invented.
"""
import os
import shutil
import signal
import sys
import time

ESC = '\x1b['
RESET = ESC + '0m'


def rgb(hex_value, background=False):
    r, g, b = (int(hex_value[i:i + 2], 16) for i in (1, 3, 5))
    return f'{ESC}{48 if background else 38};2;{r};{g};{b}m'


TEXT = rgb('#e6e6e6')
DIM = rgb('#8a8a8a')
FAINT = rgb('#5c5c5c')
GREEN = rgb('#4eba65')
RED = rgb('#ff6b80')
ORANGE = rgb('#d77757')
BLUE = rgb('#b1b9f9')
BOLD = ESC + '1m'
ADD_BG = rgb('#22432b', True)
DEL_BG = rgb('#4f2229', True)
PROMPT_BG = rgb('#373737', True)


def bullet(color, title, detail=''):
    return f'{color}⏺{RESET} {BOLD}{TEXT}{title}{RESET}{TEXT}{detail}{RESET}'


def result(text, first=True):
    lead = '  ⎿  ' if first else '     '
    return f'{DIM}{lead}{RESET}{TEXT}{text}{RESET}'


def diff(number, sign, code, width):
    if sign == '+':
        body = f'{number:>5} {GREEN}+{TEXT}  {code}'
        bg = ADD_BG
    elif sign == '-':
        body = f'{number:>5} {RED}-{TEXT}  {code}'
        bg = DEL_BG
    else:
        return f'     {DIM}{number:>5}    {code}{RESET}'
    plain = len(f'{number:>5} +  {code}')
    return f'     {bg}{TEXT}{body}{" " * max(0, width - 7 - plain)}{RESET}'


def prompt(lead, text, width):
    return f'{PROMPT_BG}{DIM}{lead}{TEXT}{text}{" " * max(0, width - len(lead + text))}{RESET}'


def lines(width):
    inner = width - 3
    out = [
        prompt('> ', 'Add per-client rate limiting to the gateway. Use a token bucket keyed by API key,', width),
        prompt('  ', 'return 429 with Retry-After, and make the limits configurable per route.', width),
        '',
        bullet(TEXT, '', "I'll look at how middleware is wired up before adding the limiter."),
        '',
        bullet(GREEN, 'Read', '(internal/middleware/chain.go)'),
        result('Read 74 lines'),
        '',
        bullet(GREEN, 'Search', '(pattern: "RateLimit|ratelimit", path: "internal")'),
        result('Found 2 files'),
        result(f'{DIM}internal/config/routes.go, internal/config/routes_test.go{RESET}', False),
        '',
        bullet(GREEN, 'Write', '(internal/middleware/ratelimit.go)'),
        result('Wrote 96 lines to internal/middleware/ratelimit.go'),
        f'     {DIM}package middleware{RESET}',
        f'     {DIM}type TokenBucket struct {{ mu sync.Mutex; buckets map[string]*bucket; rate, burst float64 }}{RESET}',
        f'     {DIM}… +91 lines (ctrl+r to expand){RESET}',
        '',
        bullet(GREEN, 'Update', '(internal/middleware/chain.go)'),
        result(f'Updated {BOLD}internal/middleware/chain.go{RESET}{TEXT} with 3 additions and 1 removal'),
        diff(41, ' ', 'func Chain(cfg *config.Gateway, next http.Handler) http.Handler {', width),
        diff(42, ' ', '    h := next', width),
        diff(43, '-', 'h = Logging(h)', width),
        diff(43, '+', 'limiter := NewTokenBucket(cfg.RateLimit)', width),
        diff(44, '+', 'h = limiter.Middleware(cfg.Routes, h)', width),
        diff(45, '+', 'h = Logging(h)', width),
        diff(46, ' ', '    return Recover(h)', width),
        '',
        bullet(GREEN, 'Bash', '(go test ./internal/middleware/... -run RateLimit -count=1)'),
        result(f'{GREEN}ok{TEXT}   example.dev/api-gateway/internal/middleware   0.412s'),
        result(f'{DIM}=== RUN   TestRateLimit_AllowsBurst  --- PASS (0.00s){RESET}', False),
        result(f'{DIM}=== RUN   TestRateLimit_Returns429WithRetryAfter  --- PASS (0.01s){RESET}', False),
        '',
        bullet(GREEN, 'Update', '(config/gateway.example.yaml)'),
        result(f'Updated {BOLD}config/gateway.example.yaml{RESET}{TEXT} with 6 additions'),
        diff(18, '+', 'rate_limit:', width),
        diff(19, '+', '  requests_per_second: 20', width),
        diff(20, '+', '  burst: 40', width),
        diff(21, '+', '  routes: { "/v1/search": { requests_per_second: 5 } }', width),
        '',
        bullet(TEXT, '', 'The limiter is in place and the unit tests pass. Summary so far:'),
        f'  {TEXT}- {BOLD}ratelimit.go{RESET}{TEXT}: token bucket per API key, idle buckets evicted after 10 minutes{RESET}',
        f'  {TEXT}- Requests over the limit get {BOLD}429{RESET}{TEXT} with a {BOLD}Retry-After{RESET}{TEXT} header in seconds{RESET}',
        f'  {TEXT}- Per-route overrides come from {BOLD}rate_limit.routes{RESET}{TEXT}; defaults apply elsewhere{RESET}',
        f'  {TEXT}Next I\'m running the integration suite against the local stack.{RESET}',
        '',
        bullet(GREEN, 'Bash', '(make integration-test)'),
        result(f'{DIM}Running… 38 of 52 tests passed{RESET}'),
        '',
        f'{ORANGE}✻{RESET} {ORANGE}Running integration tests…{RESET} {DIM}(esc to interrupt · 1m 12s · ↓ 4.8k tokens){RESET}',
        '',
        f'{FAINT}╭{"─" * inner}╮{RESET}',
        f'{FAINT}│{RESET} {TEXT}>{RESET} {" " * (inner - 3)}{FAINT}│{RESET}',
        f'{FAINT}╰{"─" * inner}╯{RESET}',
        f'  {BLUE}⏵⏵ accept edits on{RESET} {DIM}(shift+tab to cycle){RESET}',
    ]
    return out


def draw(*_):
    size = shutil.get_terminal_size((150, 50))
    body = lines(size.columns - 1)
    # Bottom-align like a scrolled conversation.
    body = body[-(size.lines - 1):]
    pad = max(0, size.lines - 1 - len(body))
    sys.stdout.write(ESC + '?25l' + ESC + '2J' + ESC + 'H' + '\n' * pad +
                     '\r\n'.join(' ' + line for line in body) + RESET)
    sys.stdout.flush()


signal.signal(signal.SIGWINCH, draw)
draw()
while True:
    time.sleep(3600)
