# Zenodo dataset record, v1 — form fields and description

The dataset record is uploaded by hand through the Zenodo web form
(`upload_type: dataset`). The GitHub integration only handles repository
releases, so nothing here is generated automatically. This file holds the
exact text and metadata that went into the record, so v2 can be diffed
against it instead of rewritten from memory.

Artifacts come from:

    python3 scripts/package_dataset.py data/sofia data/sofia/static \
        2026-08-27 2026-09-02 <out-dir>

## Before uploading

Every `⟪TBD⟫` below is a number that only exists once 2026-09-02 closes at
Sofia midnight. Fill them from the manifests, do not retype them from an
earlier run:

    python3 -c "
    import json,glob
    for f in sorted(glob.glob('data/sofia/2026-0*.manifest.json')):
        m=json.load(open(f))
        print(f\"{m['date']}: {m['total_vehicle_records']:,} records, coverage {m['coverage_pct']}%, {m['gap_count']} gap(s)\")"

## Form fields

| Field | Value |
|---|---|
| Upload type | Dataset |
| Title | Sofia public transport: raw GTFS-Realtime vehicle positions and static feed snapshots, 2026-08-27 to 2026-09-02 |
| Authors | Suslov, Stanislav · ORCID `0009-0001-8916-0822` · Independent researcher |
| Description | the HTML block below |
| License | Creative Commons Attribution 4.0 International (CC BY 4.0) |
| Access right | Open |
| Version | 1.0.0 |
| Language | English |
| Publication date | date of upload |
| Dates | Collected: 2026-08-27 to 2026-09-02 |
| Keywords | public transport, GTFS, GTFS-RT, open data, urban mobility, transit data, vehicle positions, Sofia, Bulgaria |

### Related identifiers

The relation vocabulary is DataCite's, and Zenodo's form exposes a subset of
it that changes between releases. Preferred relation first, fallback second;
pick from what the dropdown actually offers `[verify in the form]`.

| Identifier | Relation | Fallback |
|---|---|---|
| `10.5281/zenodo.22256653` (code and methodology, concept DOI) | is documented by | is supplement to |
| `https://github.com/StasSuslov/sofia-pt-data` | is compiled by | is supplement to |

After the dataset DOI exists, add the reverse link to the code record and put
the dataset DOI into `README.md` and `METHODOLOGY.md`.

## Description

Paste as HTML. Limitations come first by design: a reader deciding whether
this archive can answer their question needs to know what is missing before
they know what is in it.

```html
<p>Raw GTFS-Realtime vehicle positions for Sofia's surface public transport, polled every 30 to 60 seconds between 2026-08-27 and 2026-09-02, with the static GTFS feed snapshots observed over the same week and a per-poll heartbeat log for every day. Collection is continuous and this record is the first weekly cut of a growing archive.</p>

<p><strong>Limitations, before the contents</strong></p>
<ul>
<li>2026-08-27 covers 52.45% of its calendar day. Collection began at 11:07 local time.</li>
<li>Until 17:04 local on 2026-08-28 the collector filtered positions through a hand-picked bounding box that was narrower than the network on all four sides. Checked against the 2026-08-27 static snapshot, it excluded 281 of the feed's 4,468 stops (6.29%) and 83,664 of its 785,972 shape points (10.64%), so vehicles serving the outlying settlements were discarded as the coordinate-teleport artifact the filter exists to catch. The filter drops a record before it reaches disk, so nothing survives to reconstruct: 2026-08-27, and 2026-08-28 up to that hour, are incomplete at the edges of the network and cannot be repaired. The box in use since (lat 42.45 to 42.90, lon 23.03 to 23.66) contains every stop and shape point in that snapshot.</li>
<li>No static feed snapshots exist for 2026-08-28, 2026-08-29 and 2026-08-30. Daily archiving started after those dates, and the publisher serves only the current build without keeping history, so they cannot be recovered. Of the 32 shapes whose geometry changed between the 27 August and 31 August snapshots, an unknown share changed inside that window.</li>
<li>The feed reports <code>speed_ms</code> in kilometres per hour, not metres per second as the GTFS-RT specification requires. Values are integers with median 17, p99 56 and maximum 87; read as m/s that would be a median of 61 km/h and a maximum of 313 km/h for a city bus. The raw files keep the field under the name the feed uses, since renaming it after the fact would misrepresent what was received. This is a conclusion drawn from the data, not a statement by the feed's publisher.</li>
<li><code>bearing</code> is absent from 100% of records.</li>
<li>Sofia's metro is not present in the GTFS-RT feed. Findings from this archive describe surface transport only.</li>
<li>Feed gaps (dropped connections, empty responses) are logged in each day's heartbeat file and counted in its manifest. They are not filled in or estimated over.</li>
</ul>

<p><strong>Contents</strong></p>
<ul>
<li><code>sofia-rt_2026-08-27_2026-09-02.zip</code>: seven daily <code>&lt;date&gt;.jsonl</code> files of vehicle positions, each with its <code>&lt;date&gt;.polls.jsonl</code> heartbeat log and <code>&lt;date&gt;.manifest.json</code>. Every day file is stored uncompressed inside the zip, so an extracted file hashes to exactly what its manifest declares. 2026-08-27 has no heartbeat log: it predates heartbeat logging, and its manifest records that.</li>
<li><code>sofia-gtfs-static_2026-08-27_2026-09-02.zip</code>: four static GTFS snapshots (<code>gtfs_2026-08-27.zip</code>, <code>gtfs_2026-08-31.zip</code>, <code>gtfs_2026-09-01.zip</code>, <code>gtfs_2026-09-02.zip</code>) with their manifests, stored without recompression. A snapshot is archived only when the feed's contents change, which is why there are four rather than seven.</li>
<li><code>METHODOLOGY.md</code>, <code>README.md</code>, <code>SHA256SUMS.txt</code>.</li>
</ul>

<p><strong>Record schema</strong></p>
<p>One JSON object per line: <code>snapshot_ts</code>, <code>vehicle_id</code>, <code>route_id</code>, <code>trip_id</code>, <code>lat</code>, <code>lon</code>, <code>bearing</code>, <code>speed_ms</code>, <code>vehicle_ts</code>. A single <code>snapshot_ts</code> is stamped once per poll, so all records from one poll share it.</p>

<p><strong>Per day</strong></p>
<ul>
<li>2026-08-27: 445,780 records, coverage 52.45%, 4 gaps, no heartbeat log</li>
<li>2026-08-28: 698,420 records, coverage 100%, 0 gaps</li>
<li>2026-08-29: 467,016 records, coverage 100%, 0 gaps</li>
<li>2026-08-30: 463,271 records, coverage 100%, 0 gaps</li>
<li>2026-08-31: 730,167 records, coverage 100%, 0 gaps</li>
<li>2026-09-01: 733,951 records, coverage 100%, 0 gaps</li>
<li>2026-09-02: 735,347 records, coverage 100%, 0 gaps</li>
</ul>
<p>Coverage is the number of polls observed on the day divided by the number expected for its calendar day at the <em>configured</em> polling interval, never at the interval observed in the data. A collector quietly degrading to half rate would otherwise recalibrate its own idea of normal and report full coverage.</p>

<p><strong>Verification</strong></p>
<p>Each day carries a manifest with the SHA256 of its data and heartbeat files, the poll counts behind its coverage figure, and the observed gaps. Manifest hashes describe the uncompressed bytes in every case. <code>SHA256SUMS.txt</code> covers both archives and both documents, so <code>sha256sum -c SHA256SUMS.txt</code> checks the whole download. Nothing in these archives was packed without first being checked against its own manifest.</p>

<p><strong>Source</strong></p>
<p>Collected from the open data portal of Sofia Municipality (urbandata.sofia.bg), which publishes the feeds of the Centre for Urban Mobility under CC BY 4.0, without registration. Feed publisher: Theoremus. This record redistributes that data under the same licence and adds the collection timestamps, heartbeat logs and integrity manifests.</p>

<p><strong>Code and methodology</strong></p>
<p>The collector, the processing pipeline and the written methodology are archived separately: <a href="https://doi.org/10.5281/zenodo.22256653">10.5281/zenodo.22256653</a>. Cite both when citing a result derived from this data.</p>
```

## Upload checklist

1. Run the packaging script over `2026-08-27 2026-09-02`, verify `sha256sum -c` in the output directory.
2. Fill both `⟪TBD⟫` lines from the 2026-09-02 manifest.
3. Upload five files: two zips, `METHODOLOGY.md`, `README.md`, `SHA256SUMS.txt`. Zenodo caps a record at 50 GB and 100 files; neither binds here.
4. Set the fields above, paste the description as HTML, publish.
5. Add the reverse related identifier on the code record, then put the new DOI into `README.md`, `METHODOLOGY.md` and `CLAUDE.md` section 8.
