"""
3D点群ビューア + 計測ツール
Open3D を使って LAS/GLB ファイルを3D表示し、
クリックで計測点を選んで距離・面積の計算と結線（ライン表示）を行う。

操作方法:
  Shift + 左クリック  … 計測点を追加
  ウィンドウを閉じる  … 計測を終了して結果ウィンドウを表示
"""

import os
import threading
import numpy as np

MAX_DISPLAY_PTS = 500_000   # 表示上限点数（間引き）


# ──────────────────────────────────────────
# 内部ユーティリティ
# ──────────────────────────────────────────

def _pts_dict_to_xyz_rgb(pts_dict: dict, max_pts: int):
    """las_processor.load_points() の戻り値 → (N,3) xyz / (N,3) rgb[0-1]"""
    x = np.asarray(pts_dict["x"], dtype=np.float64)
    y = np.asarray(pts_dict["y"], dtype=np.float64)
    z = np.asarray(pts_dict["z"], dtype=np.float64)
    xyz = np.column_stack([x, y, z])

    if pts_dict.get("has_color"):
        r = np.asarray(pts_dict["r"], dtype=np.float64)
        g = np.asarray(pts_dict["g"], dtype=np.float64)
        b = np.asarray(pts_dict["b"], dtype=np.float64)
        mx = max(r.max(), g.max(), b.max(), 1e-9)
        if mx > 1.0:
            r, g, b = r / mx, g / mx, b / mx
        rgb = np.column_stack([r, g, b])
    else:
        # RGB なし → 高さで青→赤グラデーション
        zn = (z - z.min()) / max(float(z.ptp()), 1e-9)
        rgb = np.column_stack([zn, np.zeros(len(z)), 1.0 - zn])

    if len(xyz) > max_pts:
        idx = np.random.choice(len(xyz), max_pts, replace=False)
        xyz = xyz[idx]
        rgb = rgb[idx]

    return xyz, rgb


def _calc_measurements(picked_pts: np.ndarray) -> list[str]:
    """選択点の距離・合計延長・面積を計算してテキストリストで返す。"""
    results = []
    n = len(picked_pts)

    for i in range(n - 1):
        d = float(np.linalg.norm(picked_pts[i + 1] - picked_pts[i]))
        results.append(f"点{i + 1} → 点{i + 2}   距離: {d:.4f} m")

    if n >= 3:
        total = sum(
            float(np.linalg.norm(picked_pts[i + 1] - picked_pts[i]))
            for i in range(n - 1)
        )
        results.append(f"合計延長: {total:.4f} m")

        # ファン三角分割で面積（3D多角形）
        area = 0.0
        for i in range(1, n - 1):
            v1 = picked_pts[i] - picked_pts[0]
            v2 = picked_pts[i + 1] - picked_pts[0]
            area += float(np.linalg.norm(np.cross(v1, v2))) / 2.0
        results.append(f"面積（多角形）: {area:.4f} m²")

    return results


def _estimate_sphere_radius(xyz: np.ndarray) -> float:
    if len(xyz) == 0:
        return 0.02
    return float(xyz.ptp(axis=0).max()) * 0.0008


def _make_lineset(picked_pts: np.ndarray):
    """計測点を結ぶ LineSet（赤橙色）を返す。"""
    import open3d as o3d
    n = len(picked_pts)
    ls = o3d.geometry.LineSet()
    ls.points = o3d.utility.Vector3dVector(picked_pts)
    ls.lines = o3d.utility.Vector2iVector([[i, i + 1] for i in range(n - 1)])
    ls.paint_uniform_color([1.0, 0.3, 0.0])
    return ls


def _make_spheres(picked_pts: np.ndarray, radius: float):
    """計測点に黄色の小球を置いた TriangleMesh リストを返す。"""
    import open3d as o3d
    spheres = []
    for pt in picked_pts:
        s = o3d.geometry.TriangleMesh.create_sphere(radius=radius)
        s.translate(pt)
        s.paint_uniform_color([1.0, 1.0, 0.0])
        s.compute_vertex_normals()
        spheres.append(s)
    return spheres


def _show_result_window(pcd, picked_pts: np.ndarray,
                        results: list[str], log_cb=None):
    """計測結果ウィンドウ（点群 + 結線 + 計測球）を表示する。"""
    import open3d as o3d

    if log_cb:
        log_cb("=== 計測結果 ===")
        for r in results:
            log_cb(r)
        log_cb("================")

    radius = _estimate_sphere_radius(np.asarray(pcd.points))
    ls = _make_lineset(picked_pts)
    spheres = _make_spheres(picked_pts, radius)

    title = " | ".join(results[:3])
    o3d.visualization.draw_geometries(
        [pcd, ls] + spheres,
        window_name=f"計測結果   {title[:80]}",
        width=1280, height=800,
        zoom=0.7,
    )


# ──────────────────────────────────────────
# 公開 API
# ──────────────────────────────────────────

def launch_viewer(file_path, log_cb=None,
                  max_pts: int = MAX_DISPLAY_PTS) -> None:
    """
    3D点群ビューアを起動して計測を行う（ブロッキング呼び出し）。

    Parameters
    ----------
    file_path : str | list[str]
        LAS / GLB / OBJ / PLY ファイルのパス（複数の場合はリスト）
    log_cb : callable or None
        ログ出力コールバック（GUI の App._log を想定）
    max_pts : int
        表示する最大点数。超える場合はランダム間引き。
    """
    try:
        import open3d as o3d
    except ImportError:
        msg = ("open3d がインストールされていません。\n"
               "コマンドプロンプトで: pip install open3d")
        if log_cb:
            log_cb(msg)
        return

    # 単一パスもリストに統一
    if isinstance(file_path, (list, tuple)):
        paths = [p for p in file_path if p]
    else:
        paths = [file_path] if file_path else []

    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        if log_cb:
            log_cb(f"ファイルが見つかりません: {missing[0]}")
        return
    if not paths:
        if log_cb:
            log_cb("ファイルが指定されていません")
        return

    if log_cb:
        log_cb(f"3Dビューア: 読み込み中… ({len(paths)} ファイル)")

    try:
        import las_processor as lp
        if len(paths) == 1:
            pts_dict = lp.load_points(paths[0])
        else:
            pts_list = []
            for i, p in enumerate(paths):
                if log_cb:
                    log_cb(f"  [{i + 1}/{len(paths)}] {os.path.basename(p)}")
                pts_list.append(lp.load_points(p))
            pts_dict = lp.merge_points(pts_list)
    except Exception as e:
        if log_cb:
            log_cb(f"読み込みエラー: {e}")
        return

    xyz, rgb = _pts_dict_to_xyz_rgb(pts_dict, max_pts)

    if log_cb:
        log_cb(
            f"3Dビューア起動  表示点数: {len(xyz):,}  "
            f"[Shift+クリック=計測点追加 / ウィンドウを閉じる=計測実行]"
        )

    # 大きなUTM座標を中心化（float32の精度問題を回避）
    center = xyz.mean(axis=0)
    xyz_disp = xyz - center

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(xyz_disp)
    pcd.colors = o3d.utility.Vector3dVector(rgb)

    # ──────────────── 1st window: 点選択 ────────────────
    vis = o3d.visualization.VisualizerWithEditing()
    vis.create_window(
        window_name=(
            "3D点群ビューア  "
            "[Shift+クリック=計測点追加]  "
            "[ウィンドウを閉じる → 計測実行]"
        ),
        width=1280, height=800,
    )
    vis.add_geometry(pcd)

    ro = vis.get_render_option()
    ro.point_size = 2.0
    ro.background_color = np.array([0.05, 0.05, 0.12])

    vis.run()                           # ← ここで操作（ブロック）
    picked_idx = vis.get_picked_points()
    vis.destroy_window()

    if len(picked_idx) < 2:
        if log_cb:
            log_cb(
                "計測点が足りません（2点以上必要）。"
                "Shift+クリックで点を選択してからウィンドウを閉じてください。"
            )
        return

    picked_pts = xyz_disp[list(picked_idx)]
    results = _calc_measurements(picked_pts)

    # ──────────────── 2nd window: 結果表示 ────────────────
    _show_result_window(pcd, picked_pts, results, log_cb=log_cb)


def launch_viewer_thread(file_path, log_cb=None) -> None:
    """GUIからノンブロッキングで3Dビューアを起動する。単一パスもリストも可。"""
    threading.Thread(
        target=launch_viewer,
        args=(file_path, log_cb),
        daemon=True,
    ).start()
