# From this folder to a working Windows program (step by step)

Everything below is done on **your** computer and in your web browser. You do not need to install Python
for the GitHub route - GitHub builds the Windows `.exe` files for you.

## A. One-time preparation
1. Create a free account at <https://github.com> if you do not have one.
2. Install **Git for Windows** from <https://git-scm.com/download/win> (accept all defaults).
   (Prefer no commands? Install **GitHub Desktop** instead - see "No-commands option" at the end.)

## B. Put the project on GitHub
1. On GitHub click **+ → New repository**. Name: `hussaini-secure-chat`. Choose **Private**.
   Do **not** tick "Add a README". Click **Create repository**.
2. Unzip `Hussaini_v5.zip`. Open the **`Hussaini`** folder, click the address bar, type `powershell`, press Enter.
3. Run these commands one by one (replace `YOUR-USERNAME`):

```powershell
git init
git add .
git commit -m "Hussaini Secure Chat"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/hussaini-secure-chat.git
git push -u origin main
```
   (If Git asks who you are: `git config --global user.name "Your Name"` and
   `git config --global user.email "you@example.com"`, then repeat the `commit` command.
   A browser window may ask you to sign in to GitHub - do it.)

## C. Let GitHub build the programs
1. On the repository page open the **Actions** tab. If GitHub asks to enable workflows, click the green button.
2. The run **Build Windows package** starts automatically after your push (about 5-10 minutes).
   You can also start it manually: *Build Windows package → Run workflow → Run workflow*.
3. Wait for the green tick. A **yellow warning** on "Run the tests" is not fatal (see Troubleshooting).
4. Click the finished run, scroll to **Artifacts** at the bottom, click **HussainiChat-Windows**. A zip downloads.

## D. Test it on a Windows laptop (do this before giving it to your friend)
1. Create a folder (for example `Desktop\HussainiChat`) and **extract the downloaded zip into it**.
2. Double-click **`start-demo.bat`**.
   * Windows says "Windows protected your PC": click **More info → Run anyway** (the program is not
     digitally signed; this is normal for student projects).
   * Windows Firewall asks about the server: click **Allow access**.
3. Two chat windows open. In both, the server address is `localhost:8765`.
4. Window 1: **Register** (e.g. `alice`). A window shows her **passphrase** - tick *I have saved it*.
   Window 2: register `bob` the same way. Sign in on both, add each other as friends, and chat.
5. Check these things work: online list, friend request/accept, message both ways, log `bob` out, send a
   message to him, log him in again (it arrives), create a group, logout.
6. If something fails, take a photo/screenshot and send it to me together with the text of the error.

## E. Give it to your friend
* Send him the **zip** (not loose files) with Google Drive / OneDrive. Do not email `.exe` files - Gmail blocks them.
* Also send the **source** (the `Hussaini_v5.zip` you started from, or the GitHub link) so his supervisor can
  inspect the code.
* He extracts the zip into a folder and double-clicks `start-demo.bat`. He reads `PRESENTATION_GUIDE.txt`
  (inside the zip) before the presentation.
* **Two laptops on the same Wi-Fi:** laptop 1 runs `HussainiServer.exe` and shows an address like
  `192.168.1.20:8765`. On laptop 2, open `HussainiChat.exe` and type that address in **Server**.
  (Or write it once in `settings.txt` next to the program: `server_address = 192.168.1.20:8765`.)

## F. Making a new version later
Change files, then:
```powershell
git add .
git commit -m "describe the change"
git push
```
GitHub builds again automatically. For a permanent download link, tag the version:
```powershell
git tag v1.0
git push origin v1.0
```
The zip then appears under **Releases** on the repository page.

## Troubleshooting
| Problem | What to do |
|---|---|
| Actions run is red | Open the run, click the red step, copy the last 30 lines of its log and send them to me. |
| "Run the tests" shows a yellow warning | The tests were written on Linux; this does not stop the build. If the two *Smoke test* steps are green, the `.exe` files work. |
| Antivirus deletes/blocks the `.exe` | Unsigned PyInstaller programs are sometimes flagged. Restore it / add an exception, and keep the source zip available to prove what it is. |
| `git push` asks for a password | Use the browser sign-in window, or install GitHub Desktop. |
| The chat window says "Connection failed." | The server is not running or the address is wrong (it must be `host:port`). |
| "That passphrase does not belong to this account." | Use the passphrase shown when *that* account was registered. A lost passphrase cannot be recovered. |

## No-commands option (GitHub Desktop)
Install <https://desktop.github.com>, sign in, **File → Add local repository** → choose the `Hussaini` folder
(it offers to "create a repository" - accept), **Commit to main**, then **Publish repository** (tick *Keep this
code private*). Then continue at step C.
