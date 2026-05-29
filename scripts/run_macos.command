#!/usr/bin/env bash
# Finder double-click entry point — delegates to the canonical run_Darwin.sh.
exec bash "$(dirname "$0")/run_Darwin.sh"
