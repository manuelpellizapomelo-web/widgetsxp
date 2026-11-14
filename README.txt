Y2K WIDGETS  -  Windows XP / Silver desktop widgets
====================================================

WIDGETS
  Clock (analog + digital) | Calendar | System monitor (CPU, RAM, network)
  Timer / stopwatch | Notes (saved automatically)

CONTROLS
  Drag ............ move a widget            Right-click ... menu
  Double-click title bar (or the "_" button) ... roll the widget up / down
  Tray icon: left-click hides/shows everything, right-click opens the menu
  Ctrl+Alt+W ...... hide/show every widget instantly (Windows)

RUN FROM SOURCE
  pip install -r requirements.txt
  python main.py

BUILD THE .EXE (Windows)
  Double-click build_exe.bat  ->  dist\Y2KWidgets\Y2KWidgets.exe
  (No Python on your PC? Push the project to a private GitHub repo and run the
   "Build Windows exe" workflow from the Actions tab; download the zip it makes.)

USING IT WHILE GAMING
  * It uses almost no CPU: static graphics are cached, one shared 1-second timer
    drives everything, the timer stops completely while the widgets are hidden,
    and the process runs at below-normal priority so the game always wins.
  * Windows can only draw widgets over games running in WINDOWED or BORDERLESS
    mode. In exclusive fullscreen they stay behind the game.
  * It is a normal separate window: it does not inject into or read the game.
  * Menu > Opacity makes widgets see-through; menu > Always on top keeps them
    above the game window.

SKINS
  Copy example_theme.json into the "themes" folder inside the app-data folder
  (%APPDATA%\Y2KWidgets\themes), edit the colors, restart: it shows up under
  Theme in the menu.

PRIVACY
  Everything stays on your PC (state.json in %APPDATA%\Y2KWidgets).
  The only thing it asks the internet for is the current time (NTP servers,
  one tiny request at start and then once an hour). Menu > "Time from the
  internet" turns it off. It does not change the Windows clock, it only
  corrects what the widgets show.
