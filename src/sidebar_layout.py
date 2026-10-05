#!/usr/bin/env python3
"""Toggle both sidebar gaps using the same transaction as Settings Apply."""
from agent_info import endpoint_state, state_root
from runtime import require_context


def toggle(endpoint, directory):
    import installation
    from sidebar_settings import read_settings, apply_settings
    if installation.record().get('mode') != 'managed':
        raise RuntimeError('Spacing shortcuts require managed layout.')
    previous = read_settings()
    gap = 1 if previous['spaces_gap'] == previous['agents_gap'] == 0 else 0
    apply_settings(endpoint, directory, previous | {'spaces_gap': gap, 'agents_gap': gap}, previous)
    return gap


if __name__ == '__main__':
    endpoint = require_context()
    gap = toggle(endpoint, endpoint_state(state_root(), endpoint))
    print('Sidebar spacing: ' + ('spaced' if gap else 'compact'))
