"""Сборка одного файла dist/TikTokChatTTS.exe (запускать в Windows: python build_exe.py)."""

import sys

import PyInstaller.__main__ as pyinstaller

# Эти пакеты подгружают данные и модули динамически — собираем их целиком.
COLLECT_ALL = ["TikTokLive", "TikTokLiveProto", "EulerApiSdk", "betterproto2", "edge_tts", "certifi"]
COPY_METADATA = ["edge-tts", "TikTokLive"]
HIDDEN = ["ttchat.gui", "ttchat.selftest", "tkinter", "tkinter.ttk", "tkinter.messagebox"]


def main() -> int:
    args = ["run_app.py", "--name", "TikTokChatTTS", "--onefile", "--windowed",
            "--noconfirm", "--clean"]
    for name in COLLECT_ALL:
        args += ["--collect-all", name]
    for name in COPY_METADATA:
        args += ["--copy-metadata", name]
    for name in HIDDEN:
        args += ["--hidden-import", name]
    pyinstaller.run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
