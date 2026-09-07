# Privacy Policy

**cufflink** · last updated 8 September 2026

## The short version

cufflink collects nothing, stores nothing about you, and sends nothing
anywhere. There is no account, no telemetry, no analytics, and no server for
your data to reach.

## What cufflink does with the text you read

When you select text and press the hotkey, cufflink copies that selection,
turns it into speech on your own computer, plays it, and forgets it. The text
is held in memory only while it is being read. Nothing is written to disk and
nothing is transmitted.

The speech synthesis runs locally, from a model file installed alongside the
application. It is not a cloud service. The machine can be disconnected from
the internet permanently and cufflink works exactly the same.

## What cufflink stores on your computer

One file, `settings.json`, under your local application data folder. It holds
your own preferences — which voices you chose, speed, volume, and the voice
treatments you mixed. It contains no personal information and never leaves
your machine. Deleting the app's data removes it.

## Network access

cufflink opens a listening socket on `127.0.0.1:52719` so that other
applications *on the same computer* can send it text to read aloud. This
address is the loopback interface: it is reachable only from your own machine
and is not accessible from any network.

cufflink makes no outbound network connections. The Microsoft Store package
declares no `internetClient` capability, which means Windows itself would
prevent it from reaching the internet even if it tried.

If you install cufflink from source rather than the Store, the installer
downloads the speech model once from its public release page. That is the only
network request in the project, it happens at install time, and it is a
download of files to you — nothing about you is uploaded.

## Third parties

There are none. No advertising, no analytics providers, no crash reporting, no
payment processor.

## Children

cufflink collects no data from anyone, of any age.

## Changes

Any change to this policy will appear in this file, whose history is public in
the project repository.

## Contact

Ryan Reddy — https://reddy.world
Issues and questions: https://github.com/Ryan-Reddy/murmur/issues
