# Real-run recording of the full demo (no scripting): vhs scripts/demo-real.tape
# Produces a raw capture; speed it up for the README with ffmpeg, e.g.:
#   ffmpeg -i .out/demo-real-raw.gif -vf "setpts=PTS/8,fps=12,scale=1080:-1:flags=lanczos" \
#     -loop 0 docs/assets/private-ai-platform-kit-demo-real.gif
# Requires the Calico lab: LOCAL_CNI=calico make quickstart
Output .out/demo-real-raw.gif

Set FontSize 13
Set Width 900
Set Height 520
Set Theme "Catppuccin Mocha"
Set Framerate 5

Hide
Type "./scripts/demo-real.sh"
Show
Enter
Sleep 150s
