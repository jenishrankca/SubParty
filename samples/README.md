# Test clips

## the-weird-games-5min.mp4

Cut from `C:\Users\jenis\Downloads\the-weird-games.mp4` (first 5 minutes) for UI smoke:

```bat
bin\ffmpeg.exe -y -ss 0 -i "%USERPROFILE%\Downloads\the-weird-games.mp4" -t 300 -c copy samples\the-weird-games-5min.mp4
```

Open this file in SubParty → enable Audio verify (optional) → Process.
