# -*- coding: utf-8 -*-
"""物体识别 Demo 主界面（PySide6）。

布局：
  - 左侧：摄像头实时预览 / 本地图片预览
  - 右侧：QTabWidget（识别 / 录入 / 特征库）
  - 底部：状态栏
"""
import os

import cv2
import numpy as np

# 兼容 PyQt5 / PySide6
try:
    from PySide6.QtCore import Qt, QTimer, Signal
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import (
        QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
        QLineEdit, QTabWidget, QSlider, QListWidget, QListWidgetItem,
        QFileDialog, QMessageBox, QGroupBox, QProgressBar, QStatusBar,
        QFrame, QGridLayout, QComboBox,
    )
except ImportError:
    from PyQt5.QtCore import Qt, QTimer, pyqtSignal as Signal
    from PyQt5.QtGui import QImage, QPixmap
    from PyQt5.QtWidgets import (
        QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
        QLineEdit, QTabWidget, QSlider, QListWidget, QListWidgetItem,
        QFileDialog, QMessageBox, QGroupBox, QProgressBar, QStatusBar,
        QFrame, QGridLayout, QComboBox,
    )

import config
from feature import FeatureExtractor
from library import FeatureLibrary
from recognize import recognize
from speech import Speech
from utils.camera import CameraThread, cv_to_qpixmap


class MainWindow(QMainWindow):
    # 语音识别结果信号：子线程 emit → Qt 自动切回主线程执行槽函数
    # 参数 (识别文本, 错误码)，无内容时传空字符串
    stt_result = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("物体识别 Demo")
        self.resize(1100, 700)

        # ---- 后端组件 ----
        self.extractor = FeatureExtractor()
        self.library = FeatureLibrary()
        self.speech = Speech()
        self.threshold = config.THRESHOLD

        # ---- 语音识别状态 ----
        self._stt_busy = False
        self._stt_purpose = "demo"   # "register" | "demo"
        self._stt_button = None
        self.stt_result.connect(self._on_stt_result)

        # ---- 摄像头 ----
        self.camera = None
        self.camera_on = False
        self._pending_frame = None       # 最新帧，供实时识别用
        self._infer_timer = QTimer(self)
        self._infer_timer.timeout.connect(self._do_realtime_infer)

        # ---- 录入临时状态 ----
        self._register_name = ""
        self._register_feats = []        # 待录入的特征列表
        self._register_paths = []        # 待录入图片路径（用于回显）

        self._build_ui()
        self._refresh_library_list()
        # 状态栏显示当前语音识别后端（便于现场确认是否联网可用）
        self.status.showMessage(
            "语音识别：" + self.speech.backend_status())

    # ============ 构建 UI ============
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # 顶部标题栏
        title_bar = QHBoxLayout()
        title = QLabel("物体识别 Demo")
        title.setStyleSheet("font-size:18px;font-weight:600;")
        title_bar.addWidget(title)
        title_bar.addStretch()
        self.cam_status = QLabel("摄像头未开启")
        self.cam_status.setStyleSheet("color:#888;")
        title_bar.addWidget(self.cam_status)
        root.addLayout(title_bar)

        # 主体：左预览 + 右功能区
        body = QHBoxLayout()

        # ---- 左侧预览 ----
        self.preview_label = QLabel("暂无画面")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(520, 480)
        self.preview_label.setStyleSheet(
            "background:#2b2b2b;color:#aaa;border-radius:8px;")
        self.preview_label.setScaledContents(False)
        body.addWidget(self.preview_label, stretch=3)

        # ---- 右侧 Tab ----
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_recognize_tab(), "识别")
        self.tabs.addTab(self._build_register_tab(), "录入")
        self.tabs.addTab(self._build_library_tab(), "特征库")
        body.addWidget(self.tabs, stretch=2)

        root.addLayout(body, stretch=1)

        # 底部状态栏
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("就绪 · 特征库为空")

        # 全局样式
        self.setStyleSheet(self._stylesheet())

    def _stylesheet(self):
        return """
        QPushButton { padding:6px 14px; border:1px solid #ccc; border-radius:6px; background:#fff; }
        QPushButton:hover { background:#f0f0f0; }
        QPushButton#primary { background:#378ADD; color:#fff; border:none; }
        QPushButton#primary:hover { background:#2f79c4; }
        QPushButton#danger { background:#fff; color:#d33; border:1px solid #d33; }
        QPushButton#danger:hover { background:#fdecec; }
        QLineEdit { padding:6px; border:1px solid #ccc; border-radius:6px; }
        QTabWidget::pane { border:1px solid #ddd; border-radius:8px; }
        QTabBar::tab { padding:8px 20px; }
        QGroupBox { font-weight:600; margin-top:8px; }
        """

    # ---------- 识别 Tab ----------
    def _build_recognize_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        # 识别结果卡片
        result_group = QGroupBox("识别结果")
        rl = QVBoxLayout(result_group)
        self.result_name = QLabel("—")
        self.result_name.setAlignment(Qt.AlignCenter)
        self.result_name.setStyleSheet("font-size:28px;font-weight:700;color:#0C447C;")
        self.result_score = QLabel("相似度 —")
        self.result_score.setAlignment(Qt.AlignCenter)
        self.result_score.setStyleSheet("color:#185FA5;font-size:14px;")
        rl.addWidget(self.result_name)
        rl.addWidget(self.result_score)
        lay.addWidget(result_group)

        # 图片选择 + 语音
        btn_row = QHBoxLayout()
        self.btn_open_img = QPushButton("打开图片")
        self.btn_open_img.setObjectName("primary")
        self.btn_open_img.clicked.connect(self._recognize_from_file)
        btn_row.addWidget(self.btn_open_img)

        self.btn_voice_recog = QPushButton("语音识别")
        self.btn_voice_recog.clicked.connect(self._recognize_from_speech)
        btn_row.addWidget(self.btn_voice_recog)
        lay.addLayout(btn_row)

        # 摄像头源（本地 / 手机）
        cam_group = QGroupBox("摄像头源")
        cg = QVBoxLayout(cam_group)
        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("模式"))
        self.cam_mode_combo = QComboBox()
        self.cam_mode_combo.addItem("自动（优先手机）", "auto")
        self.cam_mode_combo.addItem("仅本地摄像头", "local")
        self.cam_mode_combo.addItem("仅手机摄像头", "phone")
        src_row.addWidget(self.cam_mode_combo, stretch=1)
        cg.addLayout(src_row)

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("手机地址"))
        self.cam_url_edit = QLineEdit()
        self.cam_url_edit.setPlaceholderText("http://127.0.0.1:8080/video")
        url_row.addWidget(self.cam_url_edit, stretch=1)
        cg.addLayout(url_row)

        cam_cfg = config.load_camera_config()
        idx = self.cam_mode_combo.findData(cam_cfg["mode"])
        self.cam_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.cam_url_edit.setText(cam_cfg["phone_url"])
        self.cam_mode_combo.currentIndexChanged.connect(self._on_cam_config_changed)
        self.cam_url_edit.editingFinished.connect(self._on_cam_config_changed)

        lay.addWidget(cam_group)

        self.btn_toggle_cam = QPushButton("开启摄像头实时识别")
        self.btn_toggle_cam.clicked.connect(self._toggle_camera)
        lay.addWidget(self.btn_toggle_cam)

        # Top-K 候选
        top_group = QGroupBox("Top-3 候选")
        tl = QVBoxLayout(top_group)
        self.topk_label = QLabel("（暂无）")
        self.topk_label.setStyleSheet("color:#555;")
        tl.addWidget(self.topk_label)
        lay.addWidget(top_group)

        # 阈值滑条
        thr_box = QHBoxLayout()
        thr_box.addWidget(QLabel("识别阈值"))
        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setRange(50, 99)  # 0.50 ~ 0.99
        self.threshold_slider.setValue(int(self.threshold * 100))
        self.threshold_slider.valueChanged.connect(self._on_threshold_changed)
        thr_box.addWidget(self.threshold_slider, stretch=1)
        self.threshold_label = QLabel(f"{self.threshold:.2f}")
        thr_box.addWidget(self.threshold_label)
        lay.addLayout(thr_box)

        self.btn_speak = QPushButton("语音播报结果")
        self.btn_speak.clicked.connect(self._speak_last_result)
        lay.addWidget(self.btn_speak)

        lay.addStretch()
        return w

    # ---------- 录入 Tab ----------
    def _build_register_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        # 名称输入
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("物体名称"))
        self.reg_name_input = QLineEdit()
        self.reg_name_input.setPlaceholderText("例如：水杯")
        name_row.addWidget(self.reg_name_input, stretch=1)
        self.btn_voice_name = QPushButton("语音录入名称")
        self.btn_voice_name.clicked.connect(self._voice_input_name)
        name_row.addWidget(self.btn_voice_name)
        lay.addLayout(name_row)

        # 图片采集
        img_row = QHBoxLayout()
        self.btn_add_imgs = QPushButton("添加图片")
        self.btn_add_imgs.setObjectName("primary")
        self.btn_add_imgs.clicked.connect(self._add_register_images)
        img_row.addWidget(self.btn_add_imgs)

        self.btn_capture = QPushButton("摄像头抓拍")
        self.btn_capture.clicked.connect(self._capture_from_camera)
        img_row.addWidget(self.btn_capture)
        lay.addLayout(img_row)

        # 已添加图片列表
        self.reg_img_list = QListWidget()
        self.reg_img_list.setMaximumHeight(140)
        lay.addWidget(self.reg_img_list)

        # 进度
        self.reg_progress = QProgressBar()
        self.reg_progress.setVisible(False)
        lay.addWidget(self.reg_progress)

        # 保存
        self.btn_save = QPushButton("保存到特征库")
        self.btn_save.setObjectName("primary")
        self.btn_save.clicked.connect(self._save_register)
        lay.addWidget(self.btn_save)

        self.reg_tip = QLabel("提示：一个物体建议录入 5 张以上图片")
        self.reg_tip.setStyleSheet("color:#999;font-size:12px;")
        lay.addWidget(self.reg_tip)

        lay.addStretch()
        return w

    # ---------- 特征库 Tab ----------
    def _build_library_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        self.lib_count = QLabel("已录入 0 个物体")
        self.lib_count.setStyleSheet("font-weight:600;")
        lay.addWidget(self.lib_count)

        self.lib_list = QListWidget()
        lay.addWidget(self.lib_list, stretch=1)

        btn_row = QHBoxLayout()
        self.btn_delete = QPushButton("删除选中物体")
        self.btn_delete.setObjectName("danger")
        self.btn_delete.clicked.connect(self._delete_selected)
        btn_row.addWidget(self.btn_delete)

        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self._refresh_library_list)
        btn_row.addWidget(self.btn_refresh)
        lay.addLayout(btn_row)

        # 跨设备迁移：单个 zip 即可带走整个特征库
        backup_row = QHBoxLayout()
        self.btn_export = QPushButton("导出备份")
        self.btn_export.clicked.connect(self._export_library)
        backup_row.addWidget(self.btn_export)

        self.btn_import = QPushButton("导入备份")
        self.btn_import.clicked.connect(self._import_library)
        backup_row.addWidget(self.btn_import)
        lay.addLayout(backup_row)

        tip = QLabel("换设备时：导出备份 → 拷贝 zip → 新设备导入；"
                     "或把特征库目录设到云盘（OBJECT_REC_LIBRARY_DIR）")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#999;font-size:11px;")
        lay.addWidget(tip)

        return w

    # ============ 识别相关槽 ============
    def _recognize_from_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        # 显示图片
        pixmap = QPixmap(path)
        self._show_pixmap(pixmap)
        # 识别（用 TTA 增强特征，提升类内多实例场景的鲁棒性）
        from PIL import Image
        image = Image.open(path).convert("RGB")
        feat = self.extractor.extract_tta(image)
        self._run_recognize(feat)

    def _run_recognize(self, feat):
        result = recognize(feat, self.library, self.threshold)
        self._update_result(result)

    def _update_result(self, result):
        name = result["name"]
        score = result["score"]
        raw = result.get("raw_score", score)
        self.result_name.setText(name)
        if result["is_unknown"]:
            self.result_name.setStyleSheet("font-size:28px;font-weight:700;color:#d33;")
            self.result_score.setText(f"最高相似度 {raw:.1%}（低于阈值，判为未知）")
        else:
            self.result_name.setStyleSheet("font-size:28px;font-weight:700;color:#0C447C;")
            self.result_score.setText(f"相似度 {raw:.1%}")
        # Top-K
        if result["top_k"]:
            lines = [f"{i+1}. {t['name']}  {t['score']:.1%}" for i, t in enumerate(result["top_k"])]
            self.topk_label.setText("\n".join(lines))
        else:
            self.topk_label.setText("（特征库为空）")
        self._last_result_name = name

    def _speak_last_result(self):
        name = getattr(self, "_last_result_name", None)
        if name:
            self.speech.speak(f"这是{name}")

    def _on_threshold_changed(self, val):
        self.threshold = val / 100.0
        self.threshold_label.setText(f"{self.threshold:.2f}")

    # ---------- 摄像头 ----------
    def _toggle_camera(self):
        if not self.camera_on:
            self._start_camera()
        else:
            self._stop_camera()

    def _on_cam_config_changed(self):
        """摄像头模式/地址改动时持久化（不含密钥，可随代码提交）。"""
        mode = self.cam_mode_combo.currentData()
        url = self.cam_url_edit.text().strip()
        config.save_camera_config(mode, url)

    def _resolve_camera_sources(self):
        """根据模式返回 (主源, 回退源)。"""
        mode = self.cam_mode_combo.currentData()
        url = self.cam_url_edit.text().strip()
        local = config.CAMERA_INDEX
        if mode == "local":
            return local, None
        if mode == "phone":
            return (url or config.PHONE_CAMERA_URL), None
        # auto：优先手机，失败回退本地
        return (url or config.PHONE_CAMERA_URL), local

    def _on_camera_status(self, text):
        self.cam_status.setText(text)
        self.status.showMessage("摄像头：" + text)
        if "已连接" in text:
            self.cam_status.setStyleSheet("color:#1D9E75;")
        elif "失败" in text or "中断" in text or "不可用" in text:
            self.cam_status.setStyleSheet("color:#A32D2D;")
        else:
            self.cam_status.setStyleSheet("color:#BA7517;")

    def _start_camera(self):
        source, fallback = self._resolve_camera_sources()
        self.camera = CameraThread(source, fallback=fallback)
        self.camera.frame_bgr.connect(self._on_camera_frame)
        self.camera.status.connect(self._on_camera_status)
        self.camera.start()
        self.camera_on = True
        self.btn_toggle_cam.setText("关闭摄像头")
        self.cam_status.setText("正在连接摄像头…")
        self.cam_status.setStyleSheet("color:#BA7517;")
        self._infer_timer.start(config.CAMERA_INFER_INTERVAL_MS)

    def _stop_camera(self):
        self._infer_timer.stop()
        if self.camera:
            self.camera.stop()
            self.camera = None
        self.camera_on = False
        self.btn_toggle_cam.setText("开启摄像头实时识别")
        self.cam_status.setText("摄像头未开启")
        self.cam_status.setStyleSheet("color:#888;")

    def _on_camera_frame(self, frame):
        self._pending_frame = frame
        # 显示（低频刷新显示即可，这里每帧都更新 QPixmap 开销可控）
        pixmap = cv_to_qpixmap(frame)
        self._show_pixmap(pixmap)

    def _do_realtime_infer(self):
        if self._pending_frame is None:
            return
        feat = self.extractor.extract(self._pending_frame)
        self._run_recognize(feat)

    def _show_pixmap(self, pixmap):
        self.preview_label.setPixmap(
            pixmap.scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    # ---------- 语音识别（线程安全） ----------
    def _start_stt(self, purpose, button):
        """发起一次语音识别。回调通过 Qt 信号切回主线程。"""
        if self._stt_busy:
            self.status.showMessage("正在聆听中，请稍候…")
            return
        self._stt_busy = True
        self._stt_purpose = purpose
        self._stt_button = button
        button.setEnabled(False)
        if self.speech._online_backend == "teleai":
            tip = "正在聆听并识别（云端流式，约需数秒）…例如「这是一个水杯」"
        else:
            tip = "正在聆听…请说完整句更易识别，例如「这是一个水杯」"
        self.status.showMessage(tip)
        self.speech.recognize_speech_async(
            lambda t, e: self.stt_result.emit(t or "", e or ""),
            timeout=config.STT_TIMEOUT,
        )

    @staticmethod
    def _clean_object_name(text):
        """把口语化的识别结果提取成核心物体名。

        '这是一个水杯' → '水杯'；'录入键盘' → '键盘'。
        短词听写识别率偏低，说完整句更准，因此这里做一层口语前缀剥离。
        """
        prefixes = ("物体名称是", "名称是", "这个是", "这是", "录入",
                    "一只", "一个", "一把", "我要录入")
        t = (text or "").strip()
        changed = True
        while changed:
            changed = False
            for p in prefixes:
                if t.startswith(p) and len(t) > len(p):
                    t = t[len(p):].strip()
                    changed = True
                    break
        return t.strip("，。！？、,.!? ") or (text or "").strip()

    def _on_stt_result(self, text, err_code):
        """主线程槽：更新 UI（由信号触发，绝不跨线程操作控件）。"""
        if self._stt_button is not None:
            self._stt_button.setEnabled(True)
            self._stt_button = None
        self._stt_busy = False

        if err_code:
            self.status.showMessage(
                "语音识别失败：" + self.speech.error_message(err_code))
            return

        backend_tag = self.speech._last_backend_used
        if self._stt_purpose == "register":
            name = self._clean_object_name(text)
            self.reg_name_input.setText(name)
            self.status.showMessage(
                f"已识别名称：{name}（{backend_tag}；如有偏差可编辑输入框）")
        else:
            self.status.showMessage(f"识别到：{text}（{backend_tag}）")
            self.speech.speak(text)

    # ---------- 录入相关槽 ----------
    def _voice_input_name(self):
        self._start_stt("register", self.btn_voice_name)

    def _add_register_images(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择多张图片", "", "图片 (*.png *.jpg *.jpeg *.bmp)")
        if not paths:
            return
        self._register_paths.extend(paths)
        for p in paths:
            feat = self.extractor.extract_from_file(p)
            self._register_feats.append(feat)
            self.reg_img_list.addItem(os.path.basename(p))
        self.reg_tip.setText(f"已添加 {len(self._register_feats)} 张图片")

    def _capture_from_camera(self):
        if self._pending_frame is None:
            QMessageBox.warning(self, "提示", "请先开启摄像头（识别 Tab）")
            return
        frame = self._pending_frame
        feat = self.extractor.extract(frame)
        self._register_feats.append(feat)
        self.reg_img_list.addItem(f"抓拍 {len(self._register_feats)}")
        self.reg_tip.setText(f"已添加 {len(self._register_feats)} 张图片")

    def _save_register(self):
        name = self.reg_name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入物体名称")
            return
        if not self._register_feats:
            QMessageBox.warning(self, "提示", "请先添加至少一张图片")
            return
        self.library.add(name, self._register_feats)
        self.status.showMessage(f"已录入「{name}」共 {len(self._register_feats)} 张图片")
        self.speech.speak(f"已录入物体{name}")
        # 重置
        self._register_feats = []
        self._register_paths = []
        self.reg_img_list.clear()
        self.reg_name_input.clear()
        self.reg_tip.setText("提示：一个物体建议录入 5 张以上图片")
        self._refresh_library_list()

    # ---------- 特征库相关槽 ----------
    def _refresh_library_list(self):
        stats = self.library.object_stats()
        self.lib_list.clear()
        for name, cnt in stats.items():
            self.lib_list.addItem(f"{name}（{cnt} 张）")
        self.lib_count.setText(f"已录入 {len(stats)} 个物体")
        self.status.showMessage(f"特征库共 {len(stats)} 个物体")

    def _delete_selected(self):
        item = self.lib_list.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先选中一个物体")
            return
        name = item.text().split("（")[0]
        ret = QMessageBox.question(self, "确认", f"确定删除「{name}」吗？")
        if ret == QMessageBox.Yes:
            self.library.delete(name)
            self._refresh_library_list()

    # ---------- 特征库跨设备备份 / 恢复 ----------
    def _export_library(self):
        default = os.path.join(
            config.LIBRARY_DIR, "object_library_backup.zip")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出特征库备份", default, "备份文件 (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        ok, msg = self.library.export_zip(path)
        if ok:
            self.status.showMessage("特征库已导出：" + path)
            QMessageBox.information(self, "导出成功",
                                    "%s\n\n文件：%s" % (msg, path))
        else:
            QMessageBox.warning(self, "导出失败", msg)

    def _import_library(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择特征库备份", "", "备份文件 (*.zip)")
        if not path:
            return
        manifest, err = self.library.inspect_zip(path)
        if err:
            QMessageBox.warning(self, "备份文件无效", err)
            return
        objects = manifest.get("objects", {}) if manifest else {}
        info = ("备份内容：%d 个物体 / %d 张图片\n物体：%s\n导出时间：%s"
                % (len(objects), manifest.get("item_count", "?"),
                   "、".join(objects.keys()) or "（未知）",
                   manifest.get("exported_at", "未知")))

        choice = QMessageBox.question(
            self, "导入方式",
            info + "\n\n选择「是」= 覆盖当前特征库（换设备恢复）\n"
                   "选择「否」= 合并到当前特征库\n"
                   "选择「取消」= 不导入",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes)
        if choice == QMessageBox.Cancel:
            return
        mode = "replace" if choice == QMessageBox.Yes else "merge"
        ok, msg = self.library.import_zip(path, mode=mode)
        if ok:
            self._refresh_library_list()
            self.status.showMessage(msg)
            QMessageBox.information(self, "导入成功", msg)
        else:
            QMessageBox.warning(self, "导入失败", msg)

    # ---------- 语音识别（识别 Tab 的"语音识别"按钮） ----------
    def _recognize_from_speech(self):
        # 语音说出名称 → 显示识别文本并语音复述，用于演示语音交互
        self._start_stt("demo", self.btn_voice_recog)

    def closeEvent(self, event):
        self._stop_camera()
        super().closeEvent(event)
