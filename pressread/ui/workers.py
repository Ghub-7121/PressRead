"""后台任务调度：网络请求一律不占 UI 线程。

Tk 不是线程安全的，工作线程只把结果塞进队列，主线程用 after 轮询后再碰控件。
每个任务返回一个 Job，切换文章/离开页面时 cancel()，迟到的结果会被丢弃。
"""

from __future__ import annotations

import queue
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import tkinter as tk


class Job:
    def __init__(
        self,
        name: str = "",
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ):
        self.name = name
        self.cancelled = False
        self.done = False
        self.on_success = on_success
        self.on_error = on_error

    def cancel(self) -> None:
        self.cancelled = True


class TaskRunner:
    def __init__(self, root: tk.Misc, max_workers: int = 4):
        self.root = root
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="pressread")
        self.queue: queue.Queue[tuple[Job, bool, Any]] = queue.Queue()
        self._stopped = False
        self.root.after(50, self._pump)

    def run(
        self,
        fn: Callable[[], Any],
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        *,
        name: str = "",
    ) -> Job:
        job = Job(name, on_success, on_error)
        if self._stopped:
            return job

        def worker() -> None:
            try:
                value = fn()
            except Exception as exc:  # noqa: BLE001 - 统一交给 UI 层展示
                if not job.cancelled:
                    self.queue.put((job, False, (exc, traceback.format_exc())))
                return
            if not job.cancelled:
                self.queue.put((job, True, value))

        self.pool.submit(worker)
        return job

    # 语义别名
    submit = run

    def _pump(self) -> None:
        if self._stopped:
            return
        while True:
            try:
                job, ok, payload = self.queue.get_nowait()
            except queue.Empty:
                break
            job.done = True
            if job.cancelled:
                continue
            try:
                if ok:
                    if job.on_success:
                        job.on_success(payload)
                else:
                    exc, tb = payload
                    if job.on_error:
                        job.on_error(exc)
                    else:
                        print(tb)
            except Exception:  # 回调自身出错不应该杀死轮询
                traceback.print_exc()
        try:
            self.root.after(50, self._pump)
        except tk.TclError:
            self._stopped = True

    def shutdown(self) -> None:
        self._stopped = True
        self.pool.shutdown(wait=False, cancel_futures=True)
