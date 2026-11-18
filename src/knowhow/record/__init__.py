"""Record mode: a one-way pipeline from browser audio to extracted insights.

Each stage reads one folder and writes the next, so any stage can stop,
restart or be replaced without losing work:

    audio/<group>/<clip>.<ext>       written by the capture server
    text/<group>/<clip>.json|.txt    written by any transcriber
    output/insights/<group>/*.json   written by the extract worker
    output/insights.md               a readable digest of all insights

Files are only ever added, and each is written to a temporary name and
renamed into place, so a reader never sees half a file.
"""
