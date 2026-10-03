# README demo recording from a real run: vhs scripts/compose-demo.tape
# Requires vhs, ttyd, ffmpeg, and a running Compose stack (make compose-up).
# Nothing here is staged: the tape types one command and records its actual output.
Output docs/assets/compose-demo.gif

Set Shell "bash"
Set FontSize 14
Set Width 1100
Set Height 760
Set Theme "Catppuccin Mocha"
Set Padding 14
Set TypingSpeed 45ms
Set WindowBar Colorful

Hide
Type "cd $(git rev-parse --show-toplevel) && clear"
Enter
Show

Type "make compose-smoke"
Sleep 400ms
Enter
Wait+Screen@90s /All checks passed/
Sleep 10s
