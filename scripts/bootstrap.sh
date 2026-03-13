#!/usr/bin/env bash
set -euo pipefail

echo "Bootstrap notes:"
echo "- Install ccb separately from its upstream repository."
echo "- Install cc-connect separately from its upstream repository."
echo "- Install Superpowers from upstream and enable only the selected skills."
echo "- Then install this repository in editable mode:"
echo "  conda run -n cli pip install -e ."
