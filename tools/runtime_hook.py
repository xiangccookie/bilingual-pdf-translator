# -*- coding: utf-8 -*-
"""
PyInstaller 运行时钩子（runtime hook）—— 在**导入任何应用模块之前**执行。

解决两个「窗口版打包」的经典坑：

  坑①  打成 --windowed（无控制台）后 sys.stdout / sys.stderr 是 **None**。
        不少第三方库在 **import 阶段**就往 stdout 写东西（警告、日志），
        往 None 一写就抛 AttributeError → 进程在 main() 之前就死了，
        表现为「双击没反应 / 直接退出，日志里什么都没有」，极难排查。
        → 这里先把 stdout/stderr 换成丢弃器。

  坑②  实测：同样的源码，**--console 版能跑、--windowed 版一启动就退出**
        （连程序自己的日志都没写出来）。与其去追这个差异，不如反过来：
        **打 --console 版**（stdout 一切正常，绝不会因此崩），
        再用 Win32 API 在启动瞬间把控制台窗口隐藏掉——用户看到的就是无窗口程序。
        → 这里调用 ShowWindow(GetConsoleWindow(), SW_HIDE)。

真实日志由程序自己写文件：%LOCALAPPDATA%\\BilingualPdfTranslator\\app.log
"""
import sys


class _Sink(object):
    """只吞不吐的伪流：满足 write/flush/isatty 这几个最常被调用的方法"""

    def write(self, *args, **kwargs):
        return 0

    def flush(self):
        pass

    def isatty(self):
        return False

    def fileno(self):
        raise OSError("no console")


def _hide_console():
    """把自带的控制台窗口藏起来（只在 Windows 上有效）"""
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)      # 0 = SW_HIDE
    except Exception:
        pass


# 打包后才需要处理；直接跑源码时不干预
if getattr(sys, "frozen", False):
    _hide_console()

if sys.stdout is None:
    sys.stdout = _Sink()
if sys.stderr is None:
    sys.stderr = _Sink()
