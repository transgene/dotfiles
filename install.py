# Requires Python 3.10 or newer

import argparse
import contextlib
import fcntl
import json
import os
import pty
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
from datetime import date
import pathlib

import requests

ENV_DIRS = ["home", "work"]


@contextlib.contextmanager
def cwd(path: str):
    oldpwd = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(oldpwd)


def __set_pty_winsize(fd, row, col, xpix=0, ypix=0):
    winsize = struct.pack("HHHH", row, col, xpix, ypix)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


def __pty_read(fd):
    __set_pty_winsize(fd, 1, 80)
    return os.read(fd, 1024)


def init_argparse():
    parser = argparse.ArgumentParser(prog="dotfiles", usage="install.py ENV [OPTIONS]")
    subparsers = parser.add_subparsers(title="environments", dest="env")
    for dir in ENV_DIRS:
        subparsers.add_parser(
            dir,
            usage=f"install.py {dir} [OPTIONS]",
        )
    return parser


def install(dir: str):
    pathlib.Path(dir)
    if not os.path.exists(dir):
        raise RuntimeError(f"Directory '{dir}' not found in the cwd")

    os.makedirs(os.path.expanduser("~/.dotfiles/backups"), exist_ok=True)
    backup_dir_path = None
    today = date.today().strftime("%Y-%m-%d")
    backup_attempt = None
    while backup_dir_path is None:
        suffix = "" if backup_attempt is None else f"-{backup_attempt}"
        backup_dir_path = f"{os.path.expanduser('~/.dotfiles/backups')}/{today}{suffix}"
        try:
            os.mkdir(backup_dir_path)
        except FileExistsError:
            backup_dir_path = None
            backup_attempt = 2 if backup_attempt is None else backup_attempt + 1

    if os.path.exists(f"{dir}/windows"):
        with cwd(f"{dir}/windows"):
            __install_windows(backup_dir_path)

    if os.path.exists(f"{dir}/wsl"):
        with cwd(f"{dir}/wsl"):
            __install_wsl(backup_dir_path)


def __install_windows(backup_dir_path: str):
    pty.spawn(
        shlex.split("winget.exe configure -f config.dsc.yaml"),
        __pty_read,
    )

    powershell_check = subprocess.run(
        shlex.split("winget.exe list --id Microsoft.PowerShell"),
        capture_output=True,
        text=True,
        check=True,
    )
    if "No installed" in powershell_check.stdout:
        raise RuntimeError("Can't continue: PowerShell is not installed")

    win_user_home_path_wsl = os.path.expandvars("$USERPROFILE")
    win_user_home_path_win32 = subprocess.run(
        shlex.split(f"wslpath -w -a {win_user_home_path_wsl}"),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    win_appdata_local_path_wsl = os.path.expandvars("$LOCALAPPDATA")
    win_appdata_local_path_win32 = subprocess.run(
        shlex.split(f"wslpath -w -a {win_appdata_local_path_wsl}"),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    win_appdata_roaming_path_wsl = os.path.expandvars("$APPDATA")
    cwd_win32 = subprocess.run(
        shlex.split(f"wslpath -w -a {os.getcwd()}"),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    powershell_config_path = f"{win_user_home_path_wsl}/Documents/PowerShell"
    backup_powershell_config_path = f"{backup_dir_path}{powershell_config_path}"
    os.makedirs(backup_powershell_config_path, exist_ok=True)

    powershell_profile_path = pathlib.Path(
        f"{powershell_config_path}/Microsoft.PowerShell_profile.ps1"
    )
    if powershell_profile_path.exists(follow_symlinks=False):
        if powershell_profile_path.is_symlink():
            with open(
                f"{backup_powershell_config_path}/Microsoft.PowerShell_profile.ps1.symlink",
                "w",
            ) as f:
                try:
                    profile_link_target = str(powershell_profile_path.readlink())
                    symlink_kind = "unix"
                except OSError:
                    # Means that this is a cross-device link between Windows and WSL. We have to read it with pwsh
                    profile_link_target = subprocess.run(
                        shlex.split(
                            f"pwsh.exe -NonInteractive -NoProfile -c '& {{(Get-Item {win_user_home_path_win32}\\Documents\\PowerShell\\Microsoft.PowerShell_profile.ps1).Target}}'"
                        ),
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout.strip()
                    symlink_kind = "windows"
                if not profile_link_target:
                    raise RuntimeError(
                        "Cannot get target for PowerShell profile symlink"
                    )
                json.dump(
                    {
                        "target_path": profile_link_target,
                        "target_kind": "file",
                        "symlink_kind": symlink_kind,
                    },
                    f,
                )
        else:
            shutil.copy2(
                src=powershell_profile_path,
                dst=backup_powershell_config_path,
            )
        os.remove(powershell_profile_path)

    powershell_themes_path = pathlib.Path(f"{powershell_config_path}/themes")
    if powershell_themes_path.exists(follow_symlinks=False):
        if powershell_themes_path.is_symlink():
            with open(f"{backup_powershell_config_path}/themes.symlink", "w") as f:
                try:
                    themes_link_target = str(powershell_themes_path.readlink())
                    symlink_kind = "unix"
                except OSError:
                    # Means that this is a cross-device link between Windows and WSL. We have to read it with pwsh
                    themes_link_target = subprocess.run(
                        shlex.split(
                            f"pwsh.exe -NonInteractive -NoProfile -c '& {{(Get-Item {win_user_home_path_win32}\\Documents\\PowerShell\\themes).Target}}'"
                        ),
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout.strip()
                    symlink_kind = "windows"
                if not themes_link_target:
                    raise RuntimeError(
                        "Cannot get target for PowerShell themes directory symlink"
                    )
                json.dump(
                    {
                        "target_path": themes_link_target,
                        "target_kind": "directory",
                        "symlink_kind": symlink_kind,
                    },
                    f,
                )
            os.remove(powershell_themes_path)
        else:
            shutil.copytree(
                src=powershell_themes_path,
                dst=backup_powershell_config_path,
                dirs_exist_ok=True,
            )
            shutil.rmtree(powershell_themes_path)

    print(
        "The following warnings/errors were issued when applying PowerShell configuration:"
    )
    print(
        subprocess.run(
            shlex.split(
                f"pwsh.exe -NonInteractive -NoProfile -c '& {{New-Item -ItemType SymbolicLink -Path {win_user_home_path_win32}\\Documents\\PowerShell\\Microsoft.PowerShell_profile.ps1 -Target {cwd_win32}\\powershell\\Microsoft.PowerShell_profile.ps1}}'"
            ),
            capture_output=True,
            text=True,
            check=True,
        ).stderr
    )
    print(
        subprocess.run(
            shlex.split(
                f"pwsh.exe -NonInteractive -NoProfile -c '& {{New-Item -ItemType SymbolicLink -Path {win_user_home_path_win32}\\Documents\\PowerShell\\themes -Target {cwd_win32}\\powershell\\themes}}'"
            ),
            capture_output=True,
            text=True,
            check=True,
        ).stderr
    )
    print("PowerShell configuration applied")

    winterm_check = subprocess.run(
        shlex.split("winget.exe list --id Microsoft.WindowsTerminal"),
        capture_output=True,
        text=True,
        check=True,
    )
    if "No installed" in winterm_check.stdout:
        raise RuntimeError("Can't continue: Windows Terminal is not installed")

    winterm_config_path = f"{win_appdata_local_path_wsl}/Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState"
    backup_winterm_config_path = f"{backup_dir_path}{winterm_config_path}"
    os.makedirs(backup_winterm_config_path, exist_ok=True)
    winterm_settings_file_path = pathlib.Path(f"{winterm_config_path}/settings.json")
    if winterm_settings_file_path.exists(follow_symlinks=False):
        if winterm_settings_file_path.is_symlink():
            with open(
                f"{backup_winterm_config_path}/settings.json.symlink",
                "w",
            ) as f:
                try:
                    settings_file_link_target = str(
                        winterm_settings_file_path.readlink()
                    )
                    symlink_kind = "unix"
                except OSError:
                    # Means that this is a cross-device link between Windows and WSL. We have to read it with pwsh
                    settings_file_link_target = subprocess.run(
                        shlex.split(
                            f"pwsh.exe -NonInteractive -NoProfile -c '& {{(Get-Item {win_appdata_local_path_win32}\\Packages\\Microsoft.WindowsTerminal_8wekyb3d8bbwe\\LocalState\\settings.json).Target}}'"
                        ),
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout.strip()
                    symlink_kind = "windows"
                if not settings_file_link_target:
                    raise RuntimeError(
                        "Cannot get target for Windows Terminal settings symlink"
                    )
                json.dump(
                    {
                        "target_path": settings_file_link_target,
                        "target_kind": "file",
                        "symlink_kind": symlink_kind,
                    },
                    f,
                )
        else:
            shutil.copy2(
                src=winterm_settings_file_path,
                dst=backup_winterm_config_path,
            )
        os.remove(winterm_settings_file_path)

    print(
        "The following warnings/errors were issued when applying Windows Terminal configuration:"
    )
    print(
        subprocess.run(
            shlex.split(
                f"pwsh.exe -NonInteractive -NoProfile -c '& {{New-Item -ItemType SymbolicLink -Path {win_appdata_local_path_win32}\\Packages\\Microsoft.WindowsTerminal_8wekyb3d8bbwe\\LocalState\\settings.json -Target {cwd_win32}\\winterm\\settings.json}}'"
            ),
            capture_output=True,
            text=True,
            check=True,
        ).stderr
    )
    print("Windows Terminal configuration applied")

    totalcmd_check = subprocess.run(
        shlex.split("winget.exe list --id Ghisler.TotalCommander"),
        capture_output=True,
        text=True,
        check=True,
    )
    if "No installed" in totalcmd_check.stdout:
        raise RuntimeError("Can't continue: Total Commander is not installed")

    totalcmd_config_path = f"{win_appdata_roaming_path_wsl}/GHISLER"
    backup_totalcmd_config_path = f"{backup_dir_path}{totalcmd_config_path}"
    os.makedirs(backup_totalcmd_config_path, exist_ok=True)
    totalcmd_wincmd_file_path = pathlib.Path(f"{totalcmd_config_path}/wincmd.ini")
    if totalcmd_wincmd_file_path.exists():
        shutil.copy2(
            src=totalcmd_wincmd_file_path,
            dst=backup_totalcmd_config_path,
        )
        os.remove(totalcmd_wincmd_file_path)
    totalcmd_wcxftp_file_path = pathlib.Path(f"{totalcmd_config_path}/wcx_ftp.ini")
    if totalcmd_wcxftp_file_path.exists():
        shutil.copy2(
            src=totalcmd_wcxftp_file_path,
            dst=backup_totalcmd_config_path,
        )
        os.remove(totalcmd_wcxftp_file_path)

    # We copy the file instead of making a symlink, because Total Commander
    # likes to add "garbage" like tab history to the ini file
    shutil.copy(f"{os.getcwd()}/totalcmd/wincmd.ini", totalcmd_config_path)
    totalcmd_install_path_win32 = subprocess.run(
        shlex.split(
            f"pwsh.exe -NonInteractive -NoProfile -c '& {{(Get-ItemProperty -path \"HKCU:\\Software\\Ghisler\\Total Commander\").InstallDir}}'"
        ),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if not totalcmd_install_path_win32:
        totalcmd_install_path_win32 = subprocess.run(
            shlex.split(
                f"pwsh.exe -NonInteractive -NoProfile -c '& {{(Get-ItemProperty -path \"HKLM:\\Software\\Ghisler\\Total Commander\").InstallDir}}'"
            ),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    if not totalcmd_install_path_win32:
        raise RuntimeError(
            "Unexpected error: cannot find Total Commander install directory"
        )
    with open(totalcmd_wincmd_file_path) as f:
        lines = f.readlines()
    with open(totalcmd_wincmd_file_path, "w") as f:
        for line in lines:
            if "DUMMY_TOTALCMD_INSTALL_DIR" in line:
                line = line.replace(
                    "DUMMY_TOTALCMD_INSTALL_DIR", totalcmd_install_path_win32
                )
            f.write(line)

    wcxftp_file_path = f"{os.getcwd()}/totalcmd/wcx_ftp.ini"
    if os.path.exists(wcxftp_file_path):
        shutil.copy(wcxftp_file_path, totalcmd_config_path)

    print("Total Commander configuration applied")


def __install_wsl(backup_dir_path: str):
    zsh_check = subprocess.run(
        shlex.split("zsh --version"), check=True, capture_output=True, text=True
    ).stdout
    if "command not found" in zsh_check:
        print("Installing zsh...")
        pty.spawn(
            shlex.split("sudo apt install zsh"),
            __pty_read,
        )
    zsh_is_shell_check = os.getenv("SHELL1")
    if zsh_is_shell_check is None or "zsh" not in zsh_is_shell_check:
        print("Making zsh the default shell...")
        zsh_path = subprocess.run(
            shlex.split("which zsh"), check=True, capture_output=True, text=True
        ).stdout
        pty.spawn(
            shlex.split(f"chsh -s {zsh_path}"),
            __pty_read,
        )
    print("Zsh is ready!")

    if os.getenv("ZSH") is None:
        print("Installing Oh My Zsh...")
        with tempfile.NamedTemporaryFile(delete=False) as tmpfile:
            response = requests.get(
                "https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh"
            )
            if response.status_code != 200:
                raise RuntimeError(
                    "Couldn't download Oh My Zsh install script", response
                )
            tmpfile.write(response.content)
            tmpfile_path = tmpfile.name
        pty.spawn(
            shlex.split(f"sh {tmpfile_path}"),
            __pty_read,
        )

    zshrc_path = pathlib.Path("~/.zshrc.pre-oh-my-zsh").expanduser()
    if zshrc_path.exists():
        backup_zshrc_path = f"{backup_dir_path}{zshrc_path.parent}"
        os.makedirs(backup_zshrc_path, exist_ok=True)
        shutil.copy2(
            src=zshrc_path,
            dst=backup_zshrc_path,
        )
    ohmyzshrc_path = pathlib.Path("~/.zshrc").expanduser()
    backup_ohmyzshrc_path = f"{backup_dir_path}{ohmyzshrc_path.parent}"
    os.makedirs(backup_ohmyzshrc_path, exist_ok=True)
    shutil.copy2(
        src=ohmyzshrc_path,
        dst=backup_ohmyzshrc_path,
    )
    os.remove(ohmyzshrc_path)
    os.symlink(src=f"{os.getcwd()}/zsh/.zshrc", dst=ohmyzshrc_path)
    print("Oh My Zsh is ready!")

    powerlevel_theme_path = os.getenv(
        "ZSH_CUSTOM", os.path.expanduser("~/.oh-my-zsh/custom/themes/powerlevel10k")
    )
    if not os.path.exists(powerlevel_theme_path):
        print("Downloading the Powerlevel10k theme...")
        git_errors = subprocess.run(
            shlex.split(
                f"git clone --depth=1 https://github.com/romkatv/powerlevel10k.git {powerlevel_theme_path}"
            ),
            check=True,
            capture_output=True,
            text=True,
        ).stderr.strip()
        if git_errors:
            raise RuntimeError("Error when downloading the theme", git_errors)
        # print("Installing Powerlevel10k theme...")
        # zshrc_path = pathlib.Path("~/.zshrc").expanduser()
        # with open(zshrc_path) as f:
        #     lines = f.readlines()
        # with open(zshrc_path, "w") as f:
        #     for line in lines:
        #         if "ZSH_THEME" in line:
        #             line = 'ZSH_THEME="powerlevel10k/powerlevel10k"'
        #         f.write(line)
        # print("Preparing to configure Powerlevel10k...")
        # pty.spawn(
        #     shlex.split("p10k configure"),
        #     __pty_read,
        # )
    p10k_config_path = pathlib.Path("~/.p10k.zsh").expanduser()
    if not p10k_config_path.exists():
        os.symlink(src=f"{os.getcwd()}/zsh/.p10k.zsh", dst=p10k_config_path)
    elif not p10k_config_path.is_symlink():
        backup_p10k_config_path = f"{backup_dir_path}{p10k_config_path.parent}"
        os.makedirs(backup_p10k_config_path, exist_ok=True)
        shutil.copy2(src=p10k_config_path, dst=backup_p10k_config_path)
        os.remove(p10k_config_path)
        os.symlink(src=f"{os.getcwd()}/zsh/.p10k.zsh", dst=p10k_config_path)

    print("Powerlevel10k is ready!")


def run():
    argparser = init_argparse()
    args = argparser.parse_args()
    env = args.env
    if not env:
        argparser.print_help()
        sys.exit()
    install(env)


if __name__ == "__main__":
    run()
