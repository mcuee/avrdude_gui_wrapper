# avrdude_gui_wrapper
GUI wraper for avrdude CLI using Python and C++

Take note avrdude project is licensed under GPL 2.0.

Two versions.
1) tkinter version
2) PySide6 version

Optional dependency: pyserial for USB serial port detection (not yet implemented)

avrdude binary recommended location
1) macOS and Linux -- in the PATH
2) Windows -- in the same directory of in the PATH

Make sure avrdude.conf can be found by avrdude. For Windows, that usually means in the same directory with avrdude.exe. The GUI may have an option to let the user choose the location of avrdude.conf file.
