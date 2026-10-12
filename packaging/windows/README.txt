HUSSAINI SECURE CHAT  -  READ ME
=================================

A private desktop chat. Every message is encrypted on the sender's computer
(AES-256-GCM) before it is sent, so the server only ever sees scrambled text.


WHAT IS IN THIS FOLDER
----------------------
  HussainiServer.exe       the chat server (opens a black window with a log)
  HussainiChat.exe         the chat program people use
  start-demo.bat           one click: starts the server and two chat windows
  show-database.bat        shows what the server stores (hashes + ciphertext)
  server-tools.bat         menu for the server owner: list accounts, back up the
                           database, reset a forgotten password
  settings.txt             port number, database file, default server address
  PRESENTATION_GUIDE.txt   step-by-step script for the project defence
  data\                    the server stores accounts and messages here (chat.db)

No installation is needed. You do NOT need Python.


QUICK START (ONE COMPUTER)
--------------------------
1. Extract the whole zip folder (right-click > Extract All). Do not run
   anything from inside the zip.
2. Double-click  start-demo.bat
   - a black server window opens (leave it open)
   - two chat windows open
3. In each chat window the server address is already  localhost:8765
4. Window 1: open the REGISTER tab, fill in the form, click Create account.
   A window shows your personal PASSPHRASE.  Copy it or save it to a file,
   tick "I have saved my passphrase" and continue.
5. Window 2: register a second user the same way.
6. Sign in on both (the passphrase is already filled in right after registering).
7. In window 1 open the ONLINE tab, pick the other user, click "Send friend
   request". In window 2 open FRIENDS, pick the request, click Accept.
8. Click the friend's name and start chatting. Try Groups > New group too.


THE PASSPHRASE (IMPORTANT)
--------------------------
* It is created automatically when you register and is shown once.
* You need it every time you sign in, together with your password.
* It never leaves your computer. Nobody - not even the server - can recover
  it. If it is lost, that account's old messages cannot be read again and you
  must register a new account.
* Typing it is forgiving: capital letters, spaces and dashes do not matter.


USING TWO OR MORE COMPUTERS
---------------------------
1. Put the computers on the same Wi-Fi / network.
2. On the computer that runs the server, start HussainiServer.exe. The window
   prints a line such as   Clients on OTHER computers connect to: 192.168.1.20:8765
3. On the other computers start HussainiChat.exe and type that address into
   the Server box (host:port).
4. The first time, Windows Firewall may ask about the server. Click
   "Allow access" (private networks).


SETTINGS
--------
Open settings.txt in Notepad, change a value, save, and restart the program.
  server_port      the port the server listens on (default 8765)
  database_file    where the data is kept (default data/chat.db)
  server_address   what the chat window's Server box starts with
To start again with an empty chat, close the server and delete data\chat.db.


TROUBLESHOOTING
---------------
"Windows protected your PC" (blue SmartScreen box)
    The programs are not digitally signed. Click "More info", then "Run anyway".
    Some antivirus programs also warn about new unsigned .exe files; allow it.

"Connection failed."
    The server is not running, the address is wrong, or a firewall is in the
    way. Check that the black server window is open and that the address in
    the Server box matches the one the server printed.

"Could not start the server ... port already in use"
    Another copy of the server is already running. Close it, or change
    server_port in settings.txt (and use the same port in the Server box).

"That passphrase does not belong to this account."
    The username/password are right but the passphrase is not. Use the
    passphrase shown when this account was registered.

"Invalid username or password."  /  "Too many failed attempts"
    After 5 wrong passwords an account is locked for one minute.

A chat window closes immediately or shows an error box
    A file called hussaini_error.log is written next to the program. Send it
    to the developer.

Messages show "[Unable to decrypt this message]"
    The message was not written for you (for example it was sent before you
    joined a group) or the stored data was altered.


KNOWN LIMITATIONS (honest notes for the report)
------------------------------------------------
* The connection itself is plain TCP, not TLS. Messages are end-to-end
  encrypted, but your account PASSWORD travels unencrypted to the server, and
  the server can see who talks to whom and when. Use it on a trusted network.
* Friends' encryption keys are handed out by the server. There is no
  "safety number" check, so a malicious server could in theory swap keys.
* No forward secrecy: anyone who learns your passphrase can read all your
  past messages.
