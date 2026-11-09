"""Request Windows administrator authorization for machine certificate mode."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import subprocess
import sys


def is_admin():
    return os.name == 'nt' and bool(ctypes.windll.shell32.IsUserAnAdmin())


def current_user_sid():
    """Use the token SID so a different admin account cannot take over HKCU."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    handle = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(handle)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(handle, 1, None, 0, ctypes.byref(size))
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        token = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(handle, 1, token, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(token, ctypes.POINTER(ctypes.c_void_p))[0]
        value = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return value.value
        finally:
            kernel.LocalFree(ctypes.cast(value, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(handle)


def _launch_command(enable_page_button=False):
    executable = Path(sys.executable).resolve()
    arguments = []
    if not getattr(sys, 'frozen', False):
        windowless = executable.with_name('pythonw.exe')
        if windowless.is_file():
            executable = windowless
        arguments.append(str(Path(__file__).with_name('run.py').resolve()))
    arguments.extend(['--elevated-user', current_user_sid()])
    if enable_page_button:
        arguments.append('--page-button')
    return str(executable), subprocess.list2cmdline(arguments)


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('fMask', wintypes.ULONG),
        ('hwnd', wintypes.HWND), ('lpVerb', wintypes.LPCWSTR), ('lpFile', wintypes.LPCWSTR),
        ('lpParameters', wintypes.LPCWSTR), ('lpDirectory', wintypes.LPCWSTR),
        ('nShow', ctypes.c_int), ('hInstApp', wintypes.HINSTANCE),
        ('lpIDList', ctypes.c_void_p), ('lpClass', wintypes.LPCWSTR),
        ('hkeyClass', wintypes.HKEY), ('dwHotKey', wintypes.DWORD),
        ('hIconOrMonitor', wintypes.HANDLE), ('hProcess', wintypes.HANDLE)]


def request_elevated_start(enable_page_button=False):
    """Windows owns the UAC prompt; rejection leaves all system settings alone."""
    executable, arguments = _launch_command(enable_page_button)
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 | 0x100  # process handle, do not create a second shell error UI
    info.lpVerb = 'runas'
    info.lpFile, info.lpParameters = executable, arguments
    info.lpDirectory = str(Path(executable).parent)
    info.nShow = 1
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == 1223:  # user cancelled UAC
            return False
        raise ctypes.WinError(error)
    if info.hProcess:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(info.hProcess)
    return True


def elevated_main(arguments):
    valid = (len(arguments) in (1, 2) and
             re.fullmatch(r'S-1-\d+(?:-\d+)+', arguments[0] or '') and
             (len(arguments) == 1 or arguments[1] == '--page-button'))
    if not valid:
        return 2
    if not is_admin() or arguments[0] != current_user_sid():
        ctypes.windll.user32.MessageBoxW(None,
            '请使用当前 Windows 账户的管理员授权打开工具，不能换用其他账户接管网络设置。',
            'ClipGo 视频号', 0x10)
        return 2
    from .web_app import main
    main(enable_page_button=len(arguments) == 2)
    return 0
