#!/usr/bin/env bash
   # Clone shallow, scan a fixed set of files, write the report
   AUTOWARE_DIR=deps/autoware
   mkdir -p deps
   if [ ! -d "$AUTOWARE_DIR" ]; then
     git clone --depth=1 https://github.com/autowarefoundation/autoware_utils $AUTOWARE_DIR
   fi

   # Pick a few files to start
   for f in $AUTOWARE_DIR/autoware_utils/include/autoware_utils/**/*.hpp; do
     uv run safecpp review "$f" -f html -o "reports/$(basename $f).html" --no-llm
   done
