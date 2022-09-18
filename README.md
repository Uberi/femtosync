Femtosync
=========

Minimal, zero-dependency rsync-like tool in pure Python.

See also: [femtoshare](https://github.com/Uberi/femtoshare), a minimal, zero-dependency self-hosted file sharing server in a single Python script.

Features:

* Incremental file transfer using rsync's rolling window sync algorithm.
* Matching files based on size-and-mtime or checksums.
* Each of the two scripts is ~200 lines of code, using only modules from the Python standard library.
* Usage on iOS via [Pyto](https://apps.apple.com/us/app/pyto-python-3/id1436650069).

Quickstart:

```sh
$ # on the receiving computer
$ mkdir dest
$ wget https://raw.githubusercontent.com/Uberi/femtosync/master/femtosync-receiver.py && chmod +x femtosync-receiver.py
$ ./femtosync-receiver.py --files-dir dest
Starting server listening at 127.0.0.1:5000
On the sending computer, run this command: femtosync-sender.py --host 192.168.1.101 --port 5000 --files-dir SOME_SYNC_SOURCE_DIRECTORY

$ # on the sending computer
$ wget https://raw.githubusercontent.com/Uberi/femtosync/master/femtosync-sender.py && chmod +x femtosync-sender.py
$ ./femtosync-sender.py --host 192.168.1.101 --port 5000 --files-dir .
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

Rationale
---------

I have around 60GB of music and books on my Linux computer, and I want all of it synced to my iPhone for offline consumption. To do this, I've previously used:

* USB file transfer: occasionally worked in the past, except it would break regularly on most iOS or `libimobiledevice` updates. Also, has no sync functionality.
* [OpenDrop](https://github.com/seemoo-lab/opendrop) (an implementation of Apple's AirDrop): stopped working with iPhones approximately after iOS 15 was released. Also, has no sync functionality.
* WiFi-based file transfer via [LanDrop](https://landrop.app/): can't sync files in other apps' iOS filesystem containers - I'd have to re-transfer the entire collection over each time to make sure the other apps had the latest versions of all the files.
* [Dropbox](https://dropbox.com/): same problem as LanDrop, but also dependent on third-party cloud services.
* [Google Drive](https://drive.google.com/): same problem as LanDrop, but also dependent on third-party cloud services.
* Mobius Sync (an iOS client for the Syncthing protocol): same problem as LanDrop.
* Acrosync (an iOS port of rsync): same problem as LanDrop, but also the app hasn't been updated in 7 years and so will likely stop working in a future iOS update.
* Rsync running inside Alpine Linux running inside [iSH](https://ish.app/): almost perfect, but for larger transfers, iSH would run out of memory and freeze.

Finally, I decided to write my own rsync-like client and server, using the same rolling window sync algorithm and a simple directory sync protocol on top of HTTP. It's much simpler to understand and use, but doesn't support any features unnecessary for my use case, such as syncing symlinks, permissions, owners, and groups.

I start the Femtosync server in the Pyto app on the iPhone, start the Femtosync client on the Linux computer, and the files are quickly transferred into the filesystem containers of the iOS apps that they're destined for. The [iOS Shortcuts app](https://support.apple.com/en-ca/guide/shortcuts/welcome/ios) can automate the process of starting the Femtosync server on the iOS side, while a shell script automates the process of starting the Femtosync client on the Linux side.

License
-------

Copyright 2022-2022 [Anthony Zhang (Uberi)](http://anthonyz.ca).

The source code is available online at [GitHub](https://github.com/Uberi/femtosync).

This program is made available under the MIT license. See ``LICENSE.txt`` in the project's root directory for more information.
