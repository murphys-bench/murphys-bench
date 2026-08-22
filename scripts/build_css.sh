#!/usr/bin/env bash
# Retired in v0.14.0: the front-end is Tabler, vendored under static/vendor,
# nothing to compile. This stub stays for ONE release because a box upgrading
# from v0.13.x runs its OLD scripts/update.sh, which checks out the new tag and
# then calls this script from the new tree; a missing file there would roll
# every upgrade back. Delete after v0.15.0.
exit 0
