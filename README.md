# MakeMKV-AutoRip-Script
A bash script for automatically ripping movies and TV shows using [MakeMKV](https://www.makemkv.com/).
It has been written with the focus on parallelization, so that you can rip from multiple drives at once.

Disks will automatically be ejected once they're finished and newly inserted disks will automatically be ripped with the predefined, global parameters in the settings.cfg file.

# Disclaimer
This script has only been tested on Ubuntu 20.04.01. Whilst it might work with other systems, I can't guarantee for it.

# Installation:
Install MakeMKV: [Linux Installation Docs](https://www.makemkv.com/forum/viewtopic.php?t=224)

The packages mentioned in their docs don't seem to be totally up to date, I recommend this bundle:
`apt-get install build-essential pkg-config libc6-dev libssl-dev libexpat1-dev libavcodec-dev libgl1-mesa-dev qtbase5-dev zlib1g-dev nasm libfdk-aac-dev sed wget curl tar setcd`

This set features the libavcodec, so keep you'd be best of installing MakeMKV with support for it (see "OPTIONAL: Building with latest libavcodec" and "with libfdk-aac support" in their [docs](https://www.makemkv.com/forum/viewtopic.php?t=224)).

It also includes some **necessary packages** for this script to run: `sed grep setcd`.
Make sure these are present on your system before running the script.

Start MakeMKVCon at least once before using this script: `/usr/bin/makemkvcon`.
This will make sure the necessary config files are being created properly and that the installation has succeeded.

Download the scripts and setting file to a directory of your choice and make the scripts executable.
Don't forget to adjust the settings.cfg file to your liking, especially the [license key](https://www.makemkv.com/forum/viewtopic.php?t=1053).
Then, just run the wrapper and you're good to go: `bash wrapper.sh`

Happy ripping!

# Web Status Dashboard
The wrapper starts a live status dashboard at `http://<hostname>:<webport>` (default port 8080), configurable via `webport` in `settings.cfg`.

It shows active rips with per-title and overall progress bars, which title number is being saved out of how many total, current operation, elapsed time, and a history of recent completions. Updates are pushed in real time over a WebSocket.

**Required packages:**
```
apt install python3-fastapi uvicorn
```

# Note
If you just want to rip a single disc with your predefined settings, you can call the autorip.sh-script directly, by passing the drive's location as an argument: `bash autorip.sh /dev/sr0`.
This will rip the disc the same way as with the wrapper, but just once, without all the sweet automation.

# Roadmap
* Automated install
* Custom naming schemes
* Check for dependencies
