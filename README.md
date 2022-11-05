Femtosync
=========

Minimal, zero-dependency rsync-like tool in pure Python.

See also: [femtoshare](https://github.com/Uberi/femtoshare), a minimal, zero-dependency self-hosted file sharing server in a single Python script.

Features:

* Incremental file transfer using rsync's rolling window sync algorithm.
* Matching files based on size-and-mtime or checksums.
* Chunking for both incremental and non-incremental transfers - ensures bounded memory usage on receiving end.
* Each of the two scripts is ~200 lines of code, using only modules from the Python standard library.
* Usage on iOS via [Pyto](https://apps.apple.com/us/app/pyto-python-3/id1436650069).

Quickstart:

```sh
$ # on the receiving computer
$ mkdir dest
$ wget https://raw.githubusercontent.com/Uberi/femtosync/master/femtosync-receiver.py && chmod +x femtosync-receiver.py
$ ./femtosync-receiver.py dest
Starting server listening at 127.0.0.1:5000
On the sending computer, run this command: ./femtosync-sender.py --host 192.168.1.101 --port 5000 INSERT_SYNC_SOURCE_FILE_OR_DIRECTORY

$ # on the sending computer
$ wget https://raw.githubusercontent.com/Uberi/femtosync/master/femtosync-sender.py && chmod +x femtosync-sender.py
$ ./femtosync-sender.py .
creating file 1 of 4: LICENSE.txt
creating file 2 of 4: femtosync-sender.py
creating file 3 of 4: README.md
creating file 4 of 4: femtosync-receiver.py
```

Options:

    $ ./femtosync-receiver.py --help
    usage: femtosync-receiver.py [-h] [--port PORT] [--public] [--files-dir FILES_DIR]

    optional arguments:
    -h, --help            show this help message and exit
    --port PORT           local network port to listen on
    --public              listen on remote network interfaces (allows other hosts to see the website; otherwise only this host can see it)
    --files-dir FILES_DIR
                            directory to upload/download files from (prefix with @ to specify that the path is relative to the Femtosync executable)

    $ ./femtosync-sender.py --help
    usage: femtosync-sender.py [-h] [--host HOST] [--port PORT] [--files-dir FILES_DIR] [--checksum] [--dry-run] [--ios-select-directory]

    optional arguments:
    -h, --help            show this help message and exit
    --host HOST           host that the server is listening on
    --port PORT           network port that the server is listening on
    --files-dir FILES_DIR
                            directory to upload/download files from (prefix with @ to specify that the path is relative to the Femtosync executable)
    --checksum            if this flag is specified, file comparison will be performed using full checksumming rather than just comparing size and last-modified-time
    --dry-run             just print out what actions would be performed, without actually performing them
    --ios-select-directory
                            if the receiver is running in the Pyto app on iOS, trigger an iOS prompt to select the upload/download directory (this allows the server to sync files in other apps' containers on iOS, e.g. Flacbox music)

To run local tests:

```sh
$ ./test.sh
...
============================================
============= ALL TESTS PASSED =============
============================================
```

Rationale
---------

I have around 60GB of music and books on my computer, and I want all of it synced to my iPhone for offline consumption. My requirements are:

* Linux/iOS support: must work with modern  and not require any.
* Sync support: must be able to create, update, and delete files on the iPhone until it matches what's on the computer.
* Filesystem containers support: must be able to sync files directly into apps such as VLC and Kiwix. This is because there may only be room for one copy on the phone. Also, it's inconvenient to have to manually move all of those files to the right apps' filesystem containers in order to actually use them.
* Large files support: must be able to handle 60 GB+ files within a reasonable time and without failing. Incremental sync would also be useful here in case the operation gets interrupted and resumed.
* Trustworthy: either completely open source or built by a well-known developer with a good track record, and no dependency on third-party services.
* Offline: needs to work when internet access is unavailable, such as on an airplane or on the road.

| Approach                                           | Linux/iOS? | Sync? | Filesystem containers? | Large files? | Trustworthy? | Offline? | Notes |
|:---------------------------------------------------|:-----------|:------|:-----------------------|:-------------|:-------------|:---------|:------|
| USB file transfer                                  | No         | No    | No                     | Yes          | Yes          | Yes      | Breaks regularly on most iOS or `libimobiledevice` updates |
| [OpenDrop](https://github.com/seemoo-lab/opendrop) | Sort of    | No    | No                     | Yes          | Yes          | Yes      | Reverse-engineered AirDrop, stopped working with iPhones after iOS 15 |
| [LanDrop](https://landrop.app/)                    | Yes        | No    | No                     | Yes          | Yes          | Yes      | Nice UI and cross-platform |
| [Dropbox](https://dropbox.com/)                    | Yes        | Yes   | No                     | No           | No           | No       | - |
| [Google Drive](https://drive.google.com/)          | Yes        | Yes   | No                     | No           | No           | No       | - |
| Mobius Sync                                        | Yes        | Yes   | No                     | Yes          | Yes          | Yes      | This app is the closest thing to an iOS SyncThing port |
| Acrosync                                           | Yes        | Yes   | No                     | Yes          | Sort of      | Yes      | App seems unmaintained, will likely stop working in a future iOS update |
| Rsync on [iSH](https://ish.app/)                   | Yes        | Yes   | Yes                    | No           | Yes          | Yes      | For larger transfers, app runs out of memory and freezes |

Finally, I decided to write my own rsync-like client and server, using the same rolling window sync algorithm and a simple directory sync protocol on top of HTTP. It's much simpler to understand and use, but doesn't support any features unnecessary for my use case, such as syncing symlinks, permissions, owners, and groups.

I start the Femtosync server in the Pyto app on the iPhone, start the Femtosync client on the Linux computer, and the files are quickly transferred into the filesystem containers of the iOS apps that they're destined for. The [iOS Shortcuts app](https://support.apple.com/en-ca/guide/shortcuts/welcome/ios) can make this a single-tap operation on the iPhone side.

To use Femtosync offline, I simply set up an ad-hoc WiFi network on the laptop and connect the iPhone to that network. I've also had success with [linux-wifi-hotspot](https://github.com/lakinduakash/linux-wifi-hotspot) to connect the two devices on the same network - this has several benefits, such as allowing both devices to share a single metered connection, but you have to remember to set that up beforehand.

License
-------

Copyright 2022-2022 [Anthony Zhang (Uberi)](http://anthonyz.ca).

The source code is available online at [GitHub](https://github.com/Uberi/femtosync).

This program is made available under the MIT license. See ``LICENSE.txt`` in the project's root directory for more information.
