# Zenodo dataset record, v2 — form fields and description

v2 goes up as a new version of the v1 record, through the same web form. The
text and metadata below are what goes into it; diff against
`docs/zenodo-dataset-v1.md` for what changed and why.

Artifacts come from:

    python3 scripts/package_dataset.py data/sofia data/sofia/static \
        2026-09-03 2026-09-30 data/zenodo-v2

Every number in the description was read from the manifests and from the
day files in this range on 2026-10-09. None was carried over from v1.

Published 2026-10-10 as `10.5281/zenodo.23266354` (publication date on the
record: 2026-10-09). The concept DOI `10.5281/zenodo.22285128` resolves to it,
and each file's md5 on the record matches the local copy.

## Form fields

| Field | Value |
|---|---|
| Upload type | Dataset |
| Title | Sofia public transport: raw GTFS-Realtime vehicle positions and static feed snapshots, 2026-09-03 to 2026-09-30 |
| Authors | Suslov, Stanislav · ORCID `0009-0001-8916-0822` · Independent researcher |
| Description | the HTML block below |
| License | Two, as on the corrected v1: Creative Commons Attribution 4.0 International (CC BY 4.0) for `sofia-rt_*.zip`, Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0) for `sofia-gtfs-static_*.zip` |
| Access right | Open |
| Version | 2.0.0 |
| Language | English |
| Publication date | date of upload |
| Dates | Collected: 2026-09-03 to 2026-09-30 |
| Keywords | public transport, GTFS, GTFS-RT, open data, urban mobility, transit data, vehicle positions, Sofia, Bulgaria |

### Related identifiers

Same pair as v1. The new-version draft copies them from v1; check that they
survived the copy.

| Identifier | Relation |
|---|---|
| `10.5281/zenodo.22256653` (code and methodology, concept DOI) | is documented by |
| `https://github.com/StasSuslov/sofia-pt-data` | is compiled by |

## Description

```html
<p>Raw GTFS-Realtime vehicle positions for Sofia's surface public transport, polled every 45 seconds between 2026-09-03 and 2026-09-30, with the static GTFS feed snapshots observed over the same days and a per-poll heartbeat log for every day. Collection is continuous. This is the second version of the archive: version 1.0.0 holds 2026-08-27 to 2026-09-02, this one the rest of September, and each later version holds one calendar month. Versions do not overlap, so a result that spans two of them cites both.</p>

<p><strong>Limitations, before the contents</strong></p>
<ul>
<li>2026-09-07 (Monday) and 2026-09-22 (Tuesday) ran a weekend-sized timetable: 10,138 and 10,637 scheduled trips, the same counts as Sunday 6 September and Sunday 20 September, against 14,991 on the weekdays of 8 to 11 September and 16,626 on 21 September. Each is read from the static snapshot in force on that day. A weekday baseline built from this record should treat them as reduced-service days; the processing pipeline in the code repository excludes both from its weekday median for that reason.</li>
<li>The weekday timetable changes twice inside this range: 14,907 scheduled trips on 3 and 4 September, 14,991 from 8 September, and between 16,626 and 16,710 from Monday 14 September, the day before the school year opens. Weekday record counts rise with it. Compare weeks against the snapshot in force on each day, not against one another.</li>
<li>The feed reports <code>speed_ms</code> in kilometres per hour, not metres per second as the GTFS-RT specification requires. Across the 19,463,328 records in this range the values are whole numbers with median 17 and p99 57; read as m/s the median would be 61 km/h for a city bus. The maximum is 174, and 13 records exceed 100. The raw files keep the field under the name the feed uses, since renaming it after the fact would misrepresent what was received. This is a conclusion drawn from the data, not a statement by the feed's publisher.</li>
<li><code>bearing</code> is absent from 100% of records.</li>
<li>Sofia's metro is not present in the GTFS-RT feed. Findings from this archive describe surface transport only.</li>
<li>Feed gaps (dropped connections, empty responses) are logged in each day's heartbeat file and counted in its manifest. They are not filled in or estimated over. The range holds 18 failed fetches and 5 empty responses out of 53,763 polls.</li>
</ul>

<p><strong>Contents</strong></p>
<ul>
<li><code>sofia-rt_2026-09-03_2026-09-30.zip</code>: 28 daily <code>&lt;date&gt;.jsonl</code> files of vehicle positions, each with its <code>&lt;date&gt;.polls.jsonl</code> heartbeat log and <code>&lt;date&gt;.manifest.json</code>. Every day file is stored uncompressed inside the zip, so an extracted file hashes to exactly what its manifest declares. The archive is 508 MB and extracts to 4.1 GB.</li>
<li><code>sofia-gtfs-static_2026-09-03_2026-09-30.zip</code>: 27 static GTFS snapshots, <code>gtfs_2026-09-03.zip</code> to <code>gtfs_2026-09-30.zip</code>, with their manifests, stored without recompression. A snapshot is archived only when the feed's contents change. None was archived on 2026-09-07, and the 6 September snapshot is the one in force for that day. No day in the range holds a second, intraday snapshot.</li>
<li><code>METHODOLOGY.md</code>, <code>README.md</code>, <code>SHA256SUMS.txt</code>.</li>
</ul>

<p><strong>Record schema</strong></p>
<p>One JSON object per line: <code>snapshot_ts</code>, <code>vehicle_id</code>, <code>route_id</code>, <code>trip_id</code>, <code>lat</code>, <code>lon</code>, <code>bearing</code>, <code>speed_ms</code>, <code>vehicle_ts</code>. A single <code>snapshot_ts</code> is stamped once per poll, so all records from one poll share it. Positions outside the network's bounding box (lat 42.45 to 42.90, lon 23.03 to 23.66) are dropped at collection; the filter dropped no record in this range.</p>

<p><strong>Per day</strong></p>
<ul>
<li>2026-09-03: 734,096 records, coverage 100%, 0 gaps</li>
<li>2026-09-04: 732,038 records, coverage 100%, 0 gaps</li>
<li>2026-09-05: 488,234 records, coverage 100%, 0 gaps, 3 empty responses</li>
<li>2026-09-06: 482,028 records, coverage 100%, 0 gaps</li>
<li>2026-09-07: 487,635 records, coverage 100%, 0 gaps</li>
<li>2026-09-08: 738,991 records, coverage 100.05%, 0 gaps, 1 failed fetch</li>
<li>2026-09-09: 744,979 records, coverage 100%, 0 gaps</li>
<li>2026-09-10: 741,231 records, coverage 100%, 0 gaps, 2 failed fetches</li>
<li>2026-09-11: 744,878 records, coverage 100.05%, 0 gaps, 2 failed fetches</li>
<li>2026-09-12: 493,825 records, coverage 100%, 0 gaps, 1 failed fetch</li>
<li>2026-09-13: 488,373 records, coverage 100%, 0 gaps, 1 failed fetch</li>
<li>2026-09-14: 834,712 records, coverage 100%, 0 gaps</li>
<li>2026-09-15: 832,354 records, coverage 100%, 0 gaps</li>
<li>2026-09-16: 848,594 records, coverage 100%, 0 gaps</li>
<li>2026-09-17: 845,437 records, coverage 100%, 0 gaps, 1 failed fetch</li>
<li>2026-09-18: 839,165 records, coverage 100%, 0 gaps, 1 failed fetch</li>
<li>2026-09-19: 510,640 records, coverage 100%, 0 gaps, 1 failed fetch</li>
<li>2026-09-20: 501,076 records, coverage 100%, 0 gaps</li>
<li>2026-09-21: 813,487 records, coverage 100%, 0 gaps</li>
<li>2026-09-22: 509,658 records, coverage 100%, 0 gaps</li>
<li>2026-09-23: 836,885 records, coverage 100%, 0 gaps, 2 failed fetches</li>
<li>2026-09-24: 840,222 records, coverage 100%, 0 gaps</li>
<li>2026-09-25: 839,002 records, coverage 100%, 0 gaps</li>
<li>2026-09-26: 508,110 records, coverage 100%, 0 gaps</li>
<li>2026-09-27: 504,476 records, coverage 100%, 0 gaps</li>
<li>2026-09-28: 837,249 records, coverage 100%, 0 gaps, 1 failed fetch</li>
<li>2026-09-29: 839,599 records, coverage 100%, 0 gaps, 4 failed fetches, 2 empty responses</li>
<li>2026-09-30: 846,354 records, coverage 100.05%, 0 gaps, 1 failed fetch</li>
</ul>
<p>Coverage is the number of polls the collector logged on the day, failed fetches included, divided by the number expected for its calendar day at the <em>configured</em> polling interval, never at the interval observed in the data. A collector quietly degrading to half rate would otherwise recalibrate its own idea of normal and report full coverage. A 45-second interval fits 1,920 polls into a day; three days logged 1,921 and report 100.05%.</p>

<p><strong>Verification</strong></p>
<p>Each day carries a manifest with the SHA256 of its data and heartbeat files, the poll counts behind its coverage figure, and the observed gaps. Manifest hashes describe the uncompressed bytes in every case. <code>SHA256SUMS.txt</code> covers both archives and both documents, so <code>sha256sum -c SHA256SUMS.txt</code> checks the whole download. Nothing in these archives was packed without first being checked against its own manifest, and every day's hash was also checked against the collection server's copy before packing.</p>

<p><strong>Source</strong></p>
<p>Collected from the open data portal of Sofia Municipality (urbandata.sofia.bg), which publishes the feeds of the Centre for Urban Mobility without registration. Feed publisher: Theoremus. The feeds do not share one licence. The realtime vehicle positions state none of their own and take the portal's default CC BY 4.0; the static GTFS feed states Creative Commons Attribution-ShareAlike, for which the portal names no version and which METHODOLOGY.md reads as 4.0, with the evidence for that reading set out there. This record redistributes each archive under the licence its feed carries and adds the collection timestamps, heartbeat logs and integrity manifests. <code>sofia-rt_2026-09-03_2026-09-30.zip</code> falls under CC BY 4.0, <code>sofia-gtfs-static_2026-09-03_2026-09-30.zip</code> under CC BY-SA 4.0.</p>

<p><strong>Code and methodology</strong></p>
<p>The collector, the processing pipeline and the written methodology are archived separately: <a href="https://doi.org/10.5281/zenodo.22256653">10.5281/zenodo.22256653</a>. Cite both when citing a result derived from this data.</p>
```

## Upload checklist

1. Package, then `shasum -a 256 -c SHA256SUMS.txt` in `data/zenodo-v2`.
2. Open v1 through `https://doi.org/10.5281/zenodo.22285129`, press **New
   version**. The draft copies v1's metadata and starts with no files. Do not
   use **Import files**: that pulls v1's archives into v2, and versions must
   not overlap.
3. Upload five files: two zips, `METHODOLOGY.md`, `README.md`,
   `SHA256SUMS.txt`. Send the zips with `scripts/zenodo_upload.sh <draft id>
   <zip>` (a personal token with `deposit:write` in `.env.local` as
   `ZENODO_TOKEN`, revoked after publishing): the form stalled three times on
   the static zip. If an upload aborts, delete its `Pending` row before
   retrying, or the same name is refused. Deleting a pending row may answer
   504 and still succeed; check the file list.
4. Replace title, version, dates and description. The description field
   commits only on blur, so click outside it before saving. Leave the DOI
   block on "No, I need one"; after any page reload, check it again.
5. Publish. After that the files cannot change.
6. Compare each file's md5 on the record page with `md5 -r` on the local copy.
7. Check the concept DOI `10.5281/zenodo.22285128` now resolves to v2, then
   record the v2 DOI in `CLAUDE.md` section 8.
8. Only now set `-mtime +45` in `deploy/sofia-prune.service` and on the VPS.
   Every day older than 45 days is then in a published version.
