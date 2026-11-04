"""
LAS点群データ処理ツール
RS10 / Scaniverse 両対応
展開図・平面図 → DXF出力
"""

import numpy as np
import laspy
import ezdxf
from ezdxf.enums import TextEntityAlignment
from scipy.spatial import cKDTree
from PIL import Image
import os
import json
import math
from pathlib import Path

from config import Config, DEFAULT_CONFIG


def _cfg(cfg):
    """設定が未指定ならデフォルト設定を返すヘルパ。"""
    return cfg if cfg is not None else DEFAULT_CONFIG


def _embed_raster(doc, msp, img, output_path, width_m, height_m,
                  cfg, image_path=None, insert=(0, 0)):
    """
    スキャン画像(img)をPNG保存し、DXFに実寸(m単位)で貼り付ける。
    AutoCADはPNGを相対パスで参照するため、PNGはDXFと同じフォルダに置く。
    insert：左下の配置座標（一連図で複数並べるときに使用）
    戻り値：保存(参照)したPNGパス、埋め込まなければNone
    """
    if not cfg.embed_raster or img is None:
        return None
    try:
        if image_path is None:
            image_path = os.path.splitext(output_path)[0] + ".png"
        if not os.path.exists(image_path):
            Image.fromarray(img).save(image_path)
        h_px, w_px = img.shape[0], img.shape[1]
        rel = os.path.basename(image_path)  # DXFと同フォルダ前提の相対参照
        idef = doc.add_image_def(filename=rel, size_in_pixel=(w_px, h_px))
        msp.add_image(insert=insert, size_in_units=(width_m, height_m),
                      image_def=idef, rotation=0)
        return image_path
    except Exception:
        return None



def merge_image_sheet(items: list, output_path: str, cfg: Config = None,
                      max_width: float = 120.0, gap_frac: float = 0.12) -> str:
    """
    複数図面を1枚のDXFにまとめる（各図面のプレビュー画像を縦に貼り合わせる）。
    幅は固定せず、共通スケールで各図面の本来の幅（縦横比）を保って並べる。
    （展開図は長いまま、横断図は狭いまま表示）
    items : [(タイトル, PNGパス), ...]（PNGは出力DXFと同じフォルダに置くこと）
    """
    cfg = _cfg(cfg)
    doc = ezdxf.new(dxfversion=cfg.dxf_version)
    doc.units = 6
    msp = doc.modelspace()
    doc.layers.new("TITLE", dxfattribs={"color": cfg.title_color})

    imgs = []
    for title, png in items:
        if png and os.path.exists(png):
            w_px, h_px = Image.open(png).size
            imgs.append((title, png, w_px, h_px))
    if not imgs:
        doc.saveas(output_path)
        return output_path

    # 共通スケール（最も幅広い図面を max_width に。他は本来の幅のまま比例）
    max_wpx = max(w for _, _, w, _ in imgs)
    scale = max_width / max(max_wpx, 1)
    th = max(max_width * 0.02, 0.2)

    y = 0.0
    for title, png, w_px, h_px in imgs:
        W = w_px * scale
        H = h_px * scale
        y_base = y - H
        idef = doc.add_image_def(filename=os.path.basename(png),
                                 size_in_pixel=(w_px, h_px))
        msp.add_image(insert=(0, y_base), size_in_units=(W, H),
                      image_def=idef, rotation=0)
        msp.add_lwpolyline(
            [(0, y_base), (W, y_base), (W, y_base + H), (0, y_base + H), (0, y_base)],
            dxfattribs={"color": cfg.frame_color, "lineweight": 50})
        msp.add_text(title, dxfattribs={"layer": "TITLE", "height": th,
                                        "color": cfg.title_color,
                                        "insert": (0, y_base + H + th * 0.5)})
        y = y_base - (H * gap_frac + th * 2)

    doc.saveas(output_path)
    return output_path


def render_dxf_to_png(dxf_path: str, png_path: str, dpi: int = 150) -> str:
    """
    DXFの線画をPNG画像にレンダリングする（縦断図・横断図などのプレビュー用）。
    matplotlib が必要。日本語テキストは環境により文字化けすることがあるが、
    線画（断面・ライン・グリッド）はそのまま描画される。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.fonts import fonts

    doc = ezdxf.readfile(dxf_path)

    # 日本語が豆腐(□)にならないよう、テキストスタイルのフォントを日本語フォントへ差し替える
    jp_file = None
    for fam in ("Yu Gothic", "MS Gothic", "Meiryo", "BIZ UDGothic", "MS Mincho"):
        try:
            match = fonts.font_manager.find_best_match(family=fam)
        except Exception:
            match = None
        if match is not None and getattr(match, "family", None) == fam:
            jp_file = match.filename
            break
    if jp_file:
        for style in doc.styles:
            try:
                style.dxf.font = jp_file
            except Exception:
                pass

    msp = doc.modelspace()
    fig = plt.figure()
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    try:
        # draw_layout(finalize=True) は図サイズをデータの縦横比に合わせて変更する
        Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(msp, finalize=True)
        # 解像度確保：縦横比を保ったまま長辺を target インチへ拡大してから保存
        w, h = fig.get_size_inches()
        target = 18.0
        scale = target / max(w, h, 1e-6)
        fig.set_size_inches(w * scale, h * scale)
        fig.savefig(png_path, dpi=dpi, facecolor="white")
    finally:
        plt.close(fig)
    return png_path


# =====================
# LAS読み込み
# =====================
def load_las(filepath: str) -> dict:
    """LASファイルを読み込んでXYZ+カラー情報を返す"""
    las = laspy.read(filepath)
    x = np.array(las.x)
    y = np.array(las.y)
    z = np.array(las.z)

    # カラー情報（あれば）
    try:
        r = np.array(las.red) / 65535.0
        g = np.array(las.green) / 65535.0
        b = np.array(las.blue) / 65535.0
        has_color = True
    except Exception:
        r = g = b = np.ones(len(x)) * 0.5
        has_color = False

    # 強度情報（あれば）
    try:
        intensity = np.array(las.intensity) / 65535.0
    except Exception:
        intensity = np.ones(len(x)) * 0.5

    return {
        "x": x, "y": y, "z": z,
        "r": r, "g": g, "b": b,
        "intensity": intensity,
        "has_color": has_color,
        "count": len(x),
        "x_range": (float(x.min()), float(x.max())),
        "y_range": (float(y.min()), float(y.max())),
        "z_range": (float(z.min()), float(z.max())),
    }


MESH_EXTS = (".glb", ".gltf", ".obj", ".ply", ".stl", ".fbx")


def _try_sample_uv_texture(m, samp: np.ndarray, fid: np.ndarray):
    """
    UV テクスチャから直接色をサンプリング（写真画質）。
    TextureVisuals + テクスチャ画像があれば (N,3) float32 を返す。
    テクスチャなし・失敗時は None を返し呼び出し元が頂点色にフォールバックする。
    """
    try:
        import trimesh
        import trimesh.visual.texture as _tvt
        if not isinstance(m.visual, _tvt.TextureVisuals):
            return None
        uv = np.asarray(m.visual.uv, dtype=np.float32)
        if uv is None or len(uv) != len(m.vertices):
            return None
        # テクスチャ画像を SimpleMaterial / PBRMaterial 両方から取得
        mat = getattr(m.visual, 'material', None)
        tex_img = None
        for attr in ('image', 'baseColorTexture'):
            candidate = getattr(mat, attr, None) if mat is not None else None
            if candidate is not None:
                tex_img = candidate
                break
        if tex_img is None:
            return None

        tex = np.asarray(tex_img.convert("RGB"), dtype=np.float32) / 255.0
        H, W = tex.shape[:2]

        face_uv = uv[m.faces[fid]]              # (N, 3, 2)
        verts = np.asarray(m.vertices, dtype=np.float64)
        tri = verts[m.faces[fid]]               # (N, 3, 3)

        # サンプル点の重心座標 → UV を補間
        bary = trimesh.triangles.points_to_barycentric(
            tri, np.asarray(samp, dtype=np.float64))
        bary = np.clip(bary, 0.0, 1.0)
        bary /= np.where(bary.sum(axis=1, keepdims=True) > 0,
                         bary.sum(axis=1, keepdims=True), 1.0)

        suv = (bary[:, 0:1] * face_uv[:, 0] +
               bary[:, 1:2] * face_uv[:, 1] +
               bary[:, 2:3] * face_uv[:, 2])   # (N, 2)

        # U はそのまま、V は OpenGL 慣習で反転
        px = np.clip((suv[:, 0] * (W - 1)).astype(np.int32), 0, W - 1)
        py = np.clip(((1.0 - suv[:, 1]) * (H - 1)).astype(np.int32), 0, H - 1)
        return tex[py, px].astype(np.float32)   # (N, 3)
    except Exception:
        return None


def load_mesh(filepath: str, n_samples: int = 1500000, up_axis: str = "auto") -> dict:
    """
    3Dメッシュ(GLB/OBJ/PLY等)を読み込み、面を密にサンプリングして
    load_las と同じ形式の点群dictを返す。
    UV テクスチャ（Scaniverse GLB 等）があれば写真画質で直接サンプリング。
    テクスチャがない場合は頂点色にフォールバック。
    複数メッシュ（Scene）は面積比でサンプル数を配分して個別処理。
    up_axis: "auto"=最小レンジ軸を高さに / "x"/"y"/"z" 指定も可。
    """
    import trimesh
    loaded = trimesh.load(filepath, process=False)

    # Scene → メッシュのリストに展開（各メッシュのテクスチャを保持するため個別処理）
    if isinstance(loaded, trimesh.Scene):
        if len(loaded.geometry) == 0:
            raise ValueError("メッシュが空です: %s" % filepath)
        meshes = list(loaded.geometry.values())
    else:
        meshes = [loaded]

    # 面積比でサンプル数を配分
    areas = []
    for m in meshes:
        try:
            areas.append(max(float(m.area), 1e-9))
        except Exception:
            areas.append(1e-9)
    total_area = sum(areas) or 1.0
    n_total = max(int(n_samples), 10000)

    all_V, all_col = [], []
    for m, area in zip(meshes, areas):
        n_m = max(int(n_total * area / total_area), 50)
        samp, fid = trimesh.sample.sample_surface(m, n_m)
        V = np.asarray(samp, dtype=np.float64)

        # UV テクスチャ直接サンプリング → 頂点色フォールバック
        col = _try_sample_uv_texture(m, samp, fid)
        if col is None:
            try:
                vc = np.asarray(m.visual.to_color().vertex_colors
                                )[:, :3].astype(np.float32) / 255.0
                col = vc[m.faces[fid]].mean(axis=1)
            except Exception:
                col = np.full((len(V), 3), 0.6, dtype=np.float32)

        all_V.append(V)
        all_col.append(col)

    V = np.vstack(all_V)
    col = np.vstack(all_col)

    rng = V.max(axis=0) - V.min(axis=0)
    if up_axis == "auto":
        up = int(np.argmin(rng))
    else:
        up = {"x": 0, "y": 1, "z": 2}.get(up_axis, 1)
    horiz = [i for i in range(3) if i != up]
    x = V[:, horiz[0]]; y = V[:, horiz[1]]; z = V[:, up]
    r, g, b = col[:, 0], col[:, 1], col[:, 2]
    return {
        "x": x, "y": y, "z": z,
        "r": r, "g": g, "b": b,
        "intensity": np.full(len(x), 0.5, dtype=np.float32),
        "has_color": True,
        "count": len(x),
        "x_range": (float(x.min()), float(x.max())),
        "y_range": (float(y.min()), float(y.max())),
        "z_range": (float(z.min()), float(z.max())),
    }


def load_points(filepath: str, n_samples: int = 1500000) -> dict:
    """拡張子で LAS とメッシュ(GLB/OBJ/PLY等)を自動判別して読み込む。"""
    ext = os.path.splitext(filepath)[1].lower()
    if ext in MESH_EXTS:
        return load_mesh(filepath, n_samples=n_samples)
    return load_las(filepath)


def merge_points(pts_list: list) -> dict:
    """
    複数の load_points() 戻り値を結合して1つの点群 dict にする。
    各ファイルの点群をそのまま numpy.concatenate で連結する。
    """
    if not pts_list:
        raise ValueError("結合する点群が0件です")
    if len(pts_list) == 1:
        return pts_list[0]
    keys = ("x", "y", "z", "r", "g", "b", "intensity")
    merged = {k: np.concatenate([p[k] for p in pts_list]) for k in keys}
    merged["has_color"] = any(p.get("has_color") for p in pts_list)
    merged["count"] = int(len(merged["x"]))
    for ax in ("x", "y", "z"):
        v = merged[ax]
        merged[f"{ax}_range"] = (float(v.min()), float(v.max()))
    return merged


# =====================
# 壁面の切り出し
# =====================
def extract_wall_points(pts: dict, axis: str, pos: float,
                        thickness: float = None, cfg: Config = None) -> dict:
    """
    axis: 'x' or 'y' (壁面に垂直な軸)
    pos: 壁面の位置
    thickness: 切り出し厚み(m)。Noneなら設定値(wall_thickness)を使用
    """
    cfg = _cfg(cfg)
    if thickness is None:
        thickness = cfg.wall_thickness
    if axis == "x":
        mask = np.abs(pts["x"] - pos) < thickness
    else:
        mask = np.abs(pts["y"] - pos) < thickness

    return {k: v[mask] if isinstance(v, np.ndarray) else v
            for k, v in pts.items()}


def _apply_mask(pts: dict, mask: np.ndarray) -> dict:
    return {k: v[mask] if isinstance(v, np.ndarray) else v
            for k, v in pts.items()}


def _fill_gaps(img: np.ndarray, mask: np.ndarray, cfg: Config) -> np.ndarray:
    """
    点が疎で生じた空き画素を、近傍(fill_radius_px以内)の色で補間する。
    img : (H,W,3) float配色（mask=Trueの画素は色が入っている）
    戻り値：補間後も空きのままの画素マスク（背景色を塗る対象）
    """
    empty = ~mask
    if not cfg.fill_gaps or not empty.any() or not mask.any():
        return empty
    try:
        from scipy import ndimage
    except Exception:
        return empty
    dist, (iy, ix) = ndimage.distance_transform_edt(empty, return_indices=True)
    fillm = empty & (dist <= cfg.fill_radius_px)
    if fillm.any():
        for c in range(img.shape[2]):
            ch = img[:, :, c]
            ch[fillm] = ch[iy[fillm], ix[fillm]]
    return empty & ~fillm


def _accumulate_splat(h, w, px, py, r, g, b, radius=2):
    """
    各点を1画素でなく小さな円盤(スプラット)としてガウス重みで描き込む。
    点の隙間が埋まり、3Dビューアのように滑らかな面に見える。
    戻り値: (色合計 img(H,W,3), 重み合計 wsum(H,W))  ※_finish_image にそのまま渡せる
    """
    img = np.zeros((h, w, 3), dtype=np.float32)
    wsum = np.zeros((h, w), dtype=np.float32)
    radius = max(int(radius), 0)
    sigma = max(radius, 0.6)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy > radius * radius + 0.25:
                continue                      # 円盤状に限定
            wt = float(np.exp(-(dx * dx + dy * dy) / (2.0 * sigma * sigma)))
            yy = np.clip(py + dy, 0, h - 1)
            xx = np.clip(px + dx, 0, w - 1)
            np.add.at(img, (yy, xx, 0), r * wt)
            np.add.at(img, (yy, xx, 1), g * wt)
            np.add.at(img, (yy, xx, 2), b * wt)
            np.add.at(wsum, (yy, xx), wt)
    return img, wsum


def _rasterize(h, w, px, py, r, g, b, cfg, fill_radius):
    """点群をラスタ化して仕上げる共通処理。スプラット描画＋_finish_image。"""
    sr = int(getattr(cfg, "dev_splat_radius", 2))
    if sr > 0:
        img, cnt = _accumulate_splat(h, w, px, py, r, g, b, radius=sr)
    else:
        img = np.zeros((h, w, 3), dtype=np.float32)
        cnt = np.zeros((h, w), dtype=np.float32)
        np.add.at(img, (py, px, 0), r)
        np.add.at(img, (py, px, 1), g)
        np.add.at(img, (py, px, 2), b)
        np.add.at(cnt, (py, px), 1)
    return _finish_image(img, cnt, cfg, fill_radius=fill_radius)


def _finish_image(img_sum: np.ndarray, cnt: np.ndarray, cfg: Config,
                  fill_radius: int = None) -> np.ndarray:
    """
    展開図の共通仕上げ処理。集計済み(色合計 img_sum・点数 cnt)から
    「正規化 → 孤立ノイズ除去 → 穴埋め → 色レベル補正 → 背景塗り」を行い
    0..1 の float 画像を返す。全展開図(全周/左壁/右壁)で共用する。

    方針:
    - 構造点はカットしない。除去するのは明確な孤立ノイズ画素のみ。
    - 穴は近傍色で自然に充填し、縞・砂嵐を抑える。
    """
    img = img_sum.astype(np.float32).copy()
    mask = cnt > 0
    # 1) 正規化（画素内の点色の平均）
    for c in range(3):
        ch = img[:, :, c]
        ch[mask] /= np.maximum(cnt[mask], 1)

    # 2) 孤立ノイズ除去（小さな飛び点の島だけ消す。構造は残す）
    if cfg.finish_denoise and mask.any():
        try:
            from scipy import ndimage
            lbl, n = ndimage.label(mask)
            if n > 0:
                sizes = np.bincount(lbl.ravel())
                small = np.where(sizes < max(int(cfg.finish_denoise_min_px), 1))[0]
                small = small[small != 0]
                if small.size:
                    speck = np.isin(lbl, small)
                    for c in range(3):
                        img[:, :, c][speck] = 0
                    mask = mask & ~speck
        except Exception:
            pass

    # 3) 穴埋め（滑らかな正規化畳み込みで充填＝ブロック状にならずきれい）。
    #    データのある画素はそのまま（鮮鋭を維持）、空き画素だけ近傍の重み付き平均で埋める。
    fr = int(fill_radius if fill_radius is not None else cfg.fill_radius_px)
    filled = mask.copy()
    if cfg.fill_gaps and (~mask).any() and mask.any():
        try:
            from scipy.ndimage import gaussian_filter, distance_transform_edt
            sigma = max(fr / 2.0, 1.0)
            m = mask.astype(np.float32)
            den = gaussian_filter(m, sigma)
            empty = ~mask
            dist = distance_transform_edt(empty)
            fillm = empty & (dist <= fr) & (den > 1e-4)   # 元データから fr 以内のみ充填
            if fillm.any():
                for c in range(3):
                    sm = gaussian_filter(img[:, :, c] * m, sigma)
                    img[:, :, c][fillm] = sm[fillm] / np.maximum(den[fillm], 1e-4)
                filled = mask | fillm
        except Exception:
            pass

    # 4) 色の自動レベル補正（暗い点群を見やすく。チャンネル毎に控えめに伸長）
    if cfg.finish_levels and filled.any():
        lo_p = float(cfg.finish_levels_pct)
        hi_p = 100.0 - lo_p
        for c in range(3):
            ch = img[:, :, c]
            vals = ch[filled]
            lo = np.percentile(vals, lo_p)
            hi = np.percentile(vals, hi_p)
            if hi > lo:
                ch[filled] = np.clip((ch[filled] - lo) / (hi - lo), 0.0, 1.0)

    # 5) 背景塗り
    bg = cfg.bg
    bgm = ~filled
    for c in range(3):
        img[bgm, c] = bg[c]
    return np.clip(img, 0.0, 1.0)


# =====================
# 左右分割（一連図用）
# =====================
def split_left_right(pts: dict, station_axis: str = "x", cfg: Config = None):
    """
    延長方向(station_axis)に直交する方向の中心線で点群を左右に分ける。
    水路・両側擁壁など「左右に壁がある」構造の一連図に使う。

    station_axis : 構造物の延長方向の軸 ('x' or 'y')
    戻り値：(left_pts, right_pts, center_value)
            centerは断面方向(cross軸)の中央値。左=cross<center, 右=cross>=center
    """
    cfg = _cfg(cfg)
    cross_axis = "y" if station_axis == "x" else "x"
    cross = pts[cross_axis]
    center = float(np.median(cross))
    left_mask = cross < center
    right_mask = ~left_mask
    return _apply_mask(pts, left_mask), _apply_mask(pts, right_mask), center


# =====================
# 展開図生成（擁壁：平面壁面）
# =====================
def generate_elevation_wall(pts: dict, axis: str,
                            resolution: float = None, cfg: Config = None) -> np.ndarray:
    """
    擁壁などの平面壁を展開した2D画像を生成
    axis: 壁面の水平軸 ('x' or 'y')
    resolution: 1ピクセルあたりの距離(m)。Noneなら設定値(elevation_resolution)
    """
    cfg = _cfg(cfg)
    if resolution is None:
        resolution = cfg.elevation_resolution
    if axis == "x":
        horiz = pts["x"]
    else:
        horiz = pts["y"]
    vert = pts["z"]
    r = pts["r"]
    g = pts["g"]
    b = pts["b"]

    if len(horiz) == 0:
        return np.zeros((100, 100, 3), dtype=np.uint8), {}

    h_min, h_max = float(horiz.min()), float(horiz.max())
    v_min, v_max = float(vert.min()), float(vert.max())
    W_m = max(h_max - h_min, 1e-6)
    H_m = max(v_max - v_min, 1e-6)

    # 点密度に合わせて解像度を自動調整（疎なら粗く＝穴・砂嵐を防ぐ）
    ppp = float(getattr(cfg, "dev_points_per_pixel", 1.5))
    if ppp > 0:
        density_res = float(np.sqrt(ppp * W_m * H_m / max(len(horiz), 1)))
        resolution = max(resolution, density_res)

    # ピクセル数：総ピクセル上限で制御し実寸アスペクト維持。1軸が大きすぎる時のみ解像度調整。
    w = max(int(W_m / resolution) + 1, 1)
    h = max(int(H_m / resolution) + 1, 1)
    budget = max(int(getattr(cfg, "dev_max_total_pix", 30000000)), 1)
    if w * h > budget:
        sc = (budget / (w * h)) ** 0.5
        resolution = resolution / sc
        w = max(int(W_m / resolution) + 1, 1)
        h = max(int(H_m / resolution) + 1, 1)
    axmax = int(getattr(cfg, "dev_max_axis_pix", 32000))
    if w > axmax:
        resolution = W_m / axmax; w = axmax; h = max(int(H_m / resolution) + 1, 1)
    if h > axmax:
        resolution = H_m / axmax; h = axmax; w = max(int(W_m / resolution) + 1, 1)

    px = np.clip(((horiz - h_min) / resolution).astype(int), 0, w - 1)
    py = np.clip(((v_max - vert) / resolution).astype(int), 0, h - 1)
    img = _rasterize(h, w, px, py, r, g, b, cfg, int(getattr(cfg, "dev_fill_radius_px", 8)))

    return (img * 255).astype(np.uint8), {
        "h_min": h_min, "h_max": h_max,
        "v_min": v_min, "v_max": v_max,
        "resolution": float(resolution),
        "width_m": float(W_m), "height_m": float(H_m),
    }



# =====================
# アライメント（中心線）に沿った処理：実データ（斜め・曲線の通路）対応
# =====================
def _pca_alignment(pts: dict):
    """XY平面の主成分から延長方向を求め、各点の station(延長s) と transverse(横断th) を返す。"""
    x, y = pts["x"], pts["y"]
    cx, cy = x.mean(), y.mean()
    X = np.stack([x - cx, y - cy], axis=1)
    cov = np.cov(X.T)
    w, V = np.linalg.eigh(cov)
    d = V[:, int(np.argmax(w))]          # 延長方向（最大分散）
    nrm = np.array([-d[1], d[0]])        # 横断方向
    s = X @ d
    th = X @ nrm
    return s - s.min(), th               # s:0始まり, th:中心0付近


def reorient_by_alignment(pts: dict) -> dict:
    """点群を「延長=X(0始まり), 横断=Y, 高さ=Z」に並べ替えたコピーを返す。
    斜め・任意方向の通路を、軸並行の処理（平面図・展開図）に乗せるため。"""
    s, th = _pca_alignment(pts)
    p = dict(pts)
    p["x"] = s
    p["y"] = th
    p["z"] = pts["z"]
    p["x_range"] = (float(s.min()), float(s.max()))
    p["y_range"] = (float(th.min()), float(th.max()))
    p["z_range"] = (float(pts["z"].min()), float(pts["z"].max()))
    return p


def _tube_station_mask(s, angle, n_bins=200, min_cover=0.5, sectors=12):
    """
    各測点で断面の角度カバレッジ（リングのうち点がある割合）を見て、
    「閉じた管」になっている連続区間だけを残すマスクを返す。
    坑口など開いた端（天端側が空＝カバレッジ低）を自動で外す。
    """
    L = float(s.max()) or 1.0
    sb = np.clip((s / L * n_bins).astype(int), 0, n_bins - 1)
    sec = (np.mod(angle, 2 * np.pi) / (2 * np.pi) * sectors).astype(int) % sectors
    cnt = np.bincount(sb, minlength=n_bins)
    cover = np.zeros(n_bins)
    for k in range(n_bins):
        if cnt[k] > 20:
            cover[k] = len(np.unique(sec[sb == k])) / sectors
    valid = cover >= min_cover
    # 最長の連続有効区間
    runs = []
    k = 0
    while k < n_bins:
        if valid[k]:
            j = k
            while j < n_bins and valid[j]:
                j += 1
            runs.append((k, j - 1))
            k = j
        else:
            k += 1
    if not runs:
        return np.ones(len(s), dtype=bool)
    a, b = max(runs, key=lambda r: r[1] - r[0])
    return (sb >= a) & (sb <= b)


def generate_tunnel_development(pts: dict, cfg: Config = None, seam: str = None,
                                resolution: float = None,
                                n_station_bins: int = 300) -> tuple:
    """
    トンネル/暗渠の内面を「アライメントに沿って」円周展開する（魚の開き）。
    延長方向はXY平面の主成分で自動判定（斜め・任意方向OK）。
    縦断勾配は測点ごとの断面中心で吸収する。
    横軸=展開延長、縦軸=円周の展開（seam=crownでインバート中央）。
    """
    cfg = _cfg(cfg)
    if seam is None:
        seam = cfg.tunnel_seam
    if resolution is None:
        resolution = cfg.dev_resolution
    z = pts["z"]
    r, g, b = pts["r"], pts["g"], pts["b"]
    if len(z) < 10:
        return np.zeros((100, 100, 3), dtype=np.uint8), {}

    # 中心線フレーム（測点s=曲線中心線の弧長＝実延長・圧縮なし）
    frame = _tunnel_centerline_frame(pts, n_station_bins)
    if frame is None:
        return np.zeros((100, 100, 3), dtype=np.uint8), {}
    # トンネル管だけにトリム（坑口・地形・植生など管外のクラッターを除外）→再フレーム
    rad0 = frame[5]
    R = float(getattr(cfg, "dev_trim_radius", 0.0))
    if R <= 0:
        R = float(np.percentile(rad0, 92)) * 1.15       # 自動：管外周＋少し
    keep = rad0 <= R
    if 10 <= int(keep.sum()) < len(rad0):
        ptt = {k: (v[keep] if isinstance(v, np.ndarray) and len(v) == len(rad0) else v)
               for k, v in pts.items()}
        f2 = _tunnel_centerline_frame(ptt, n_station_bins)
        if f2 is not None:
            frame = f2
            r, g, b = ptt["r"], ptt["g"], ptt["b"]
    s, L, dth, dz, angle, rad = frame
    # 坑口など「開いた端」を自動除外（閉じた管になっている連続区間だけ残す）
    if bool(getattr(cfg, "dev_auto_trim_ends", True)):
        km = _tube_station_mask(s, angle)
        if 10 <= int(km.sum()) < len(s):
            s = s[km]; s = s - float(s.min())
            dth = dth[km]; dz = dz[km]; angle = angle[km]; rad = rad[km]
            r = r[km]; g = g[km]; b = b[km]
            L = float(s.max()) or L
    # 手動の両端トリム（各端を指定m切る）
    te = float(getattr(cfg, "dev_trim_ends_m", 0.0))
    if te > 0 and L > 2 * te + 1.0:
        km = (s >= te) & (s <= L - te)
        if int(km.sum()) >= 10:
            s = s[km]; s = s - float(s.min())
            dth = dth[km]; dz = dz[km]; angle = angle[km]; rad = rad[km]
            r = r[km]; g = g[km]; b = b[km]
            L = float(s.max()) or L
    seam_ang = np.pi / 2 if seam == "crown" else -np.pi / 2
    dev = np.mod(angle - seam_ang, 2 * np.pi)
    med = float(np.median(rad)) or 1.0
    if bool(getattr(cfg, "dev_uniform", True)):
        # 均一角度展開：縦＝角度×基準半径（非円形でも空白帯なく完全表示・クリーン）
        arc = dev * med
        a_max = 2 * np.pi * med
    else:
        # 実半径展開：断面サイズの違いをそのまま出す（坑口大断面も全表示）
        arc = dev * rad
        a_max = float(arc.max()) or (2 * np.pi * med)

    # 点密度に合わせて解像度を自動調整（疎な点群で穴だらけにならないよう粗くする）。
    # 目標: 1画素あたり dev_points_per_pixel 点。目標解像度より細かくはしない。
    ppp = float(getattr(cfg, "dev_points_per_pixel", 1.5))
    if ppp > 0:
        area = max(L * a_max, 1e-6)
        density_res = float(np.sqrt(ppp * area / max(len(s), 1)))
        resolution = max(resolution, density_res)

    # ピクセル数の決定：縦をつぶさないよう「総ピクセル上限」で制御し、実寸アスペクトを維持。
    # 1軸が極端に大きい場合のみクランプ（延長端・大断面はカットしない＝解像度で調整）。
    w = max(int(L / resolution) + 1, 1)
    h = max(int(a_max / resolution) + 1, 1)
    budget = max(int(cfg.dev_max_total_pix), 1)
    if w * h > budget:
        sc = (budget / (w * h)) ** 0.5
        resolution = resolution / sc
        w = max(int(L / resolution) + 1, 1)
        h = max(int(a_max / resolution) + 1, 1)
    axmax = int(cfg.dev_max_axis_pix)
    if w > axmax:
        resolution = L / axmax
        w = axmax
        h = max(int(a_max / resolution) + 1, 1)
    if h > axmax:
        resolution = a_max / axmax
        h = axmax
        w = max(int(L / resolution) + 1, 1)

    px = np.clip((s / L * (w - 1)).astype(int), 0, w - 1)
    py = np.clip(((a_max - arc) / a_max * (h - 1)).astype(int), 0, h - 1)
    img = _rasterize(h, w, px, py, r, g, b, cfg, cfg.dev_fill_radius_px)

    return (img * 255).astype(np.uint8), {
        "h_min": 0.0, "h_max": L, "v_min": 0.0, "v_max": a_max,
        "width_m": L, "height_m": a_max, "radius": med, "seam": seam,
        "resolution": float(resolution),
    }


def _tunnel_centerline_frame(pts: dict, n_station_bins: int):
    """
    トンネル点群の測点 s（曲線中心線に沿った累積弧長＝実延長・圧縮なし）と、
    各点の断面内オフセット(dth, dz)・角度・半径を返す。
    展開系（全周/左壁/右壁）で共用する中心線フレーム計算。

    直線PCA投影は弦長になり、曲がったトンネルで距離が圧縮される。
    そこで「測点ごとのXY重心を繋いだ曲線中心線」の累積弧長を測点に用いる。
    戻り値: s, L, dth, dz, angle, rad  (Noneなら点不足)
    """
    x, y, z = pts["x"], pts["y"], pts["z"]
    s0, _th0 = _pca_alignment(pts)           # ビン分け・順序付け用の仮測点(弦)
    L0 = float(s0.max()) or 1.0
    nb = max(int(n_station_bins), 2)
    bi = np.clip((s0 / L0 * nb).astype(int), 0, nb - 1)
    cx = np.full(nb, np.nan); cy = np.full(nb, np.nan)
    zc = np.full(nb, np.nan); s0c = np.full(nb, np.nan)
    for k in range(nb):
        m = bi == k
        if m.sum() > 5:
            cx[k] = np.median(x[m]); cy[k] = np.median(y[m])
            zc[k] = np.median(z[m]); s0c[k] = np.median(s0[m])
    ii = np.arange(nb)
    v = ~np.isnan(zc)
    if v.sum() < 2:
        return None
    cx = np.interp(ii, ii[v], cx[v]); cy = np.interp(ii, ii[v], cy[v])
    zc = np.interp(ii, ii[v], zc[v]); s0c = np.interp(ii, ii[v], s0c[v])
    if nb >= 5:
        from scipy.ndimage import uniform_filter1d
        cx = uniform_filter1d(cx, size=5, mode="nearest")
        cy = uniform_filter1d(cy, size=5, mode="nearest")
        zc = uniform_filter1d(zc, size=5, mode="nearest")
    # 曲線中心線（各ビン重心）に沿った累積弧長＝実延長。弦長への圧縮を解消。
    seg = np.sqrt(np.diff(cx) ** 2 + np.diff(cy) ** 2)
    arclen = np.concatenate([[0.0], np.cumsum(seg)])
    L = float(arclen[-1]) or L0
    # 局所接線フレーム（中心線の接線に直交＝正しい横断方向）
    tx = np.gradient(cx); ty = np.gradient(cy)
    tn = np.hypot(tx, ty); tn[tn == 0] = 1.0
    tx /= tn; ty /= tn
    perpx = -ty; perpy = tx
    # 各点を「最近傍の中心線ノード」に割り当てる（s0ビンより正確）。
    # ただし全ノードから最近傍探索すると、急なカーブの内側の点が
    # カーブの離れた別区間のノードに引き寄せられて誤割当されることがある
    # （曲率半径に対してオフセットが大きいほど顕著）。そこで探索範囲を
    # s0ビン(bi)近傍のノードだけに絞り、離れた区間へ誤って飛ばないようにする。
    win = max(5, nb // 15)
    knn = np.clip(bi, 0, nb - 1).copy()
    for k in range(nb):
        m = bi == k
        if not m.sum():
            continue
        lo = max(0, k - win); hi = min(nb, k + win + 1)
        d = np.hypot(x[m][:, None] - cx[lo:hi][None, :],
                     y[m][:, None] - cy[lo:hi][None, :])
        knn[m] = lo + np.argmin(d, axis=1)
    dx = x - cx[knn]; dy = y - cy[knn]
    s = arclen[knn] + (dx * tx[knn] + dy * ty[knn])   # 測点=ノード弧長＋接線投影
    s = s - float(s.min())
    L = float(s.max()) or L0
    dth = dx * perpx[knn] + dy * perpy[knn]           # 局所横断（右+/左-）
    dz = z - zc[knn]
    rad = np.sqrt(dth ** 2 + dz ** 2)
    angle = np.arctan2(dz, dth)        # 上(+z)=+π/2=天端, 下=-π/2=インバート, 右=0, 左=±π
    return s, L, dth, dz, angle, rad


def generate_tunnel_wall_development(pts: dict, side: str = "left",
                                     cfg: Config = None, resolution: float = None,
                                     wall_split: str = None, wall_datum: str = None,
                                     n_station_bins: int = 300,
                                     flip_lr: bool = None) -> tuple:
    """
    トンネルの「左壁のみ」または「右壁のみ」を平面展開する。
    - 横軸 = 中心線沿いの実延長（曲がりを straighten）
    - 縦軸 = インバート(底)基準で壁面に沿った実アーク長（アーチ湾曲を平らに展開）
    左右は断面中心の鉛直面で二分（wall_split="centerline"）。
    side : "left" / "right"
    """
    cfg = _cfg(cfg)
    if resolution is None:
        resolution = cfg.dev_resolution
    if wall_split is None:
        wall_split = getattr(cfg, "wall_split", "centerline")
    if wall_datum is None:
        wall_datum = getattr(cfg, "wall_datum", "invert")
    if flip_lr is None:
        flip_lr = bool(getattr(cfg, "flip_lr", False))
    z = pts["z"]
    r, g, b = pts["r"], pts["g"], pts["b"]
    if len(z) < 10:
        return np.zeros((100, 100, 3), dtype=np.uint8), {}

    frame = _tunnel_centerline_frame(pts, n_station_bins)
    if frame is None:
        return np.zeros((100, 100, 3), dtype=np.uint8), {}
    # トンネル管だけにトリム（坑口・地形クラッターを除外。左右分割の偏りも防ぐ）→再フレーム
    rad0 = frame[5]
    R = float(getattr(cfg, "dev_trim_radius", 0.0))
    if R <= 0:
        R = float(np.percentile(rad0, 92)) * 1.15
    keep = rad0 <= R
    if 10 <= int(keep.sum()) < len(rad0):
        ptt = {k: (v[keep] if isinstance(v, np.ndarray) and len(v) == len(rad0) else v)
               for k, v in pts.items()}
        f2 = _tunnel_centerline_frame(ptt, n_station_bins)
        if f2 is not None:
            frame = f2
            r, g, b = ptt["r"], ptt["g"], ptt["b"]
    s, L, dth, dz, angle, rad = frame

    # インバート(底)=-π/2 を基準に CCW へ展開。dev∈[0,π]=右半分, [π,2π]=左半分
    seam_ang = -np.pi / 2
    dev = np.mod(angle - seam_ang, 2 * np.pi)
    right_mask = dev <= np.pi            # dth>=0（右壁）
    left_mask = ~right_mask              # dth<0 （左壁）

    want_left = (side == "left") ^ bool(flip_lr)
    sel = left_mask if want_left else right_mask
    if sel.sum() < 10:
        return np.zeros((100, 100, 3), dtype=np.uint8), {}

    # 各壁を「インバート(0)→天端」へ展開。均一展開なら基準半径、実半径展開なら各点半径。
    Rref = float(np.median(rad)) or 1.0
    rad_use = np.full_like(rad, Rref) if bool(getattr(cfg, "dev_uniform", True)) else rad
    if want_left:
        arc = (2 * np.pi - dev) * rad_use    # 左壁：インバートから左回りに天端へ
    else:
        arc = dev * rad_use                  # 右壁：インバートから右回りに天端へ

    ss = s[sel]
    arc = arc[sel]
    rr, gg, bb = r[sel], g[sel], b[sel]

    # datum: 既定インバート(0)。springline は起拱線(約π/2·rad)を0に寄せる
    med_r = float(np.median(rad[sel])) or 1.0
    if wall_datum == "springline":
        arc = arc - (np.pi / 2) * med_r

    a_lo = float(arc.min())
    a_hi = float(arc.max()) or (np.pi * med_r)
    a_span = max(a_hi - a_lo, 1e-6)

    # 点密度に合わせた解像度（疎なら粗く）
    ppp = float(getattr(cfg, "dev_points_per_pixel", 1.5))
    if ppp > 0:
        area = max(L * a_span, 1e-6)
        resolution = max(resolution, float(np.sqrt(ppp * area / max(len(ss), 1))))

    w = max(int(L / resolution) + 1, 1)
    h = max(int(a_span / resolution) + 1, 1)
    budget = max(int(cfg.dev_max_total_pix), 1)
    if w * h > budget:
        sc = (budget / (w * h)) ** 0.5
        resolution = resolution / sc
        w = max(int(L / resolution) + 1, 1)
        h = max(int(a_span / resolution) + 1, 1)
    axmax = int(cfg.dev_max_axis_pix)
    if w > axmax:
        resolution = L / axmax; w = axmax; h = max(int(a_span / resolution) + 1, 1)
    if h > axmax:
        resolution = a_span / axmax; h = axmax; w = max(int(L / resolution) + 1, 1)

    px = np.clip((ss / L * (w - 1)).astype(int), 0, w - 1)
    py = np.clip(((a_hi - arc) / a_span * (h - 1)).astype(int), 0, h - 1)
    img = _rasterize(h, w, px, py, rr, gg, bb, cfg, cfg.dev_fill_radius_px)

    return (img * 255).astype(np.uint8), {
        "h_min": 0.0, "h_max": L, "v_min": float(a_lo), "v_max": float(a_hi),
        "width_m": L, "height_m": float(a_span), "radius": med_r,
        "side": "left" if want_left else "right", "datum": wall_datum,
        "resolution": float(resolution),
    }


def generate_longitudinal(pts: dict, cfg: Config = None, n_bins: int = 200,
                          swap_lr: bool = False) -> dict:
    """
    アライメントに沿った縦断図データ。横=展開延長、縦=実標高。
    天端(全体の最高)、左壁天端、右壁天端、インバート(最低)、中心(中央値)を測点ごとに抽出。
    壁高が異なる箇所は左壁・右壁ラインの高さ差として表れる。

    左右の判定は「点群全体を貫く1本の直線（PCA）」ではなく、平面図上で
    測点ごとに求めた重心を繋いだ曲線中心線と、その局所接線を基準に行う。
    直線基準だと河川・水路が蛇行している区間で中心線から外れ、
    左右の割り当てが場所によって入れ替わって擁壁のラインが混ざっていた。
    曲線中心線＋局所接線なら、蛇行していても上流→下流の進行方向を
    基準に左右が常に安定する。
    swap_lr : True で左右を入れ替える（GUIの「左右ミラー」に対応。
              中心線の向きが実際の上流→下流と逆だった場合の補正用）
    """
    cfg = _cfg(cfg)
    frame = _tunnel_centerline_frame(pts, max(int(n_bins), 2))
    empty = {"crown": [], "left": [], "right": [], "floor": [], "center": []}
    if frame is None:
        return empty
    s, L, dth, dz, angle, rad = frame
    z = pts["z"]
    nb = max(int(n_bins), 2)
    bi = np.clip((s / L * nb).astype(int), 0, nb - 1)
    profile = {"crown": [], "left": [], "right": [], "floor": [], "center": []}
    for k in range(nb):
        m = bi == k
        if m.sum() < 5:
            continue
        dist = (k + 0.5) / nb * L
        zk = z[m]
        thk = dth[m]
        profile["crown"].append((dist, _robust_extreme(zk, "max", cfg.profile_outlier_pct)))
        profile["floor"].append((dist, _robust_extreme(zk, "min", cfg.profile_outlier_pct)))
        profile["center"].append((dist, float(np.median(zk))))
        # 局所接線を上流→下流と見て左（進行方向を90°CCW回転）が正
        lm = thk > 0
        rm = ~lm
        if lm.sum() > 2:
            profile["left"].append((dist, _robust_extreme(zk[lm], "max", cfg.profile_outlier_pct)))   # 左壁天端
        if rm.sum() > 2:
            profile["right"].append((dist, _robust_extreme(zk[rm], "max", cfg.profile_outlier_pct)))  # 右壁天端
    if swap_lr:
        profile["left"], profile["right"] = profile["right"], profile["left"]
    if cfg.profile_smooth_window > 1:
        for key in profile:
            profile[key] = _smooth_series(profile[key], cfg.profile_smooth_window)

    profile["tunnel_range"] = None
    if cfg.profile_show_tunnel_extent:
        valid = _tube_station_mask(s, angle, n_bins=nb,
                                    min_cover=cfg.profile_portal_min_cover,
                                    sectors=cfg.profile_portal_sectors)
        if valid.any() and not valid.all():
            t0, t1 = float(s[valid].min()), float(s[valid].max())
            total = float(s.max())
            frac = (t1 - t0) / total if total > 0 else 0.0
            if 0.15 <= frac < 0.97:
                profile["tunnel_range"] = (t0, t1)
    return profile


# =====================
# 平面図生成（真上から投影）
# =====================
def generate_plan_view(pts: dict, resolution: float = None, cfg: Config = None) -> tuple:
    """点群を真上から投影した平面図を生成。resolution Noneなら設定値(plan_resolution)"""
    cfg = _cfg(cfg)
    if resolution is None:
        resolution = cfg.plan_resolution
    x, y = pts["x"], pts["y"]
    z = pts["z"]
    r, g, b = pts["r"], pts["g"], pts["b"]

    x_min, x_max = x.min(), x.max()
    y_min, y_max = y.min(), y.max()

    w = max(int((x_max - x_min) / resolution) + 1, 1)
    h = max(int((y_max - y_min) / resolution) + 1, 1)

    MAX_PIX = cfg.plan_max_pix
    if w > MAX_PIX or h > MAX_PIX:
        scale = min(MAX_PIX / w, MAX_PIX / h)
        resolution = resolution / scale
        w = min(int((x_max - x_min) / resolution) + 1, MAX_PIX)
        h = min(int((y_max - y_min) / resolution) + 1, MAX_PIX)

    z_max_arr = np.full((h, w), -np.inf)
    color_arr = np.zeros((h, w, 3), dtype=np.float32)

    px = np.clip(((x - x_min) / resolution).astype(int), 0, w - 1)
    py = np.clip(((y_max - y) / resolution).astype(int), 0, h - 1)

    # z昇順ソートして最後（最高z）の書き込みが各画素に残るようにする。
    # numpyのファンシーインデックスは同一インデックスへの重複代入で最後の値が勝つ性質を利用。
    order = np.argsort(z)
    px_s, py_s = px[order], py[order]
    color_arr[py_s, px_s, 0] = r[order]
    color_arr[py_s, px_s, 1] = g[order]
    color_arr[py_s, px_s, 2] = b[order]
    z_max_arr[py_s, px_s] = z[order]

    bg_mask = _fill_gaps(color_arr, z_max_arr != -np.inf, cfg)
    bg = cfg.bg
    color_arr[bg_mask, 0] = bg[0]
    color_arr[bg_mask, 1] = bg[1]
    color_arr[bg_mask, 2] = bg[2]

    return (color_arr * 255).astype(np.uint8), {
        "x_min": float(x_min), "x_max": float(x_max),
        "y_min": float(y_min), "y_max": float(y_max),
        "resolution": float(resolution),
        "width_m": float(x_max - x_min),
        "height_m": float(y_max - y_min),
    }


# =====================
# DXF出力（展開図）
# =====================
def export_elevation_dxf(img: np.ndarray, meta: dict, output_path: str,
                          title: str = "展開図", cfg: Config = None,
                          image_path: str = None):
    """展開図をDXFとして出力（ラスタ埋め込み＋外枠・寸法）"""
    cfg = _cfg(cfg)
    doc = ezdxf.new(dxfversion=cfg.dxf_version)
    doc.units = 6  # meters
    msp = doc.modelspace()

    W = meta.get("width_m", 1.0)
    H = meta.get("height_m", 1.0)

    # スキャン画像を実寸で埋め込み（枠・線より下に敷く）
    _embed_raster(doc, msp, img, output_path, W, H, cfg, image_path)

    # 外枠
    msp.add_lwpolyline(
        [(0, 0), (W, 0), (W, H), (0, H), (0, 0)],
        dxfattribs={"color": cfg.frame_color, "lineweight": 50}
    )

    # タイトル
    msp.add_text(
        title,
        dxfattribs={"height": H * 0.03, "color": cfg.title_color}
    ).set_placement((W / 2, H + H * 0.05), align=TextEntityAlignment.MIDDLE_CENTER)

    # 横寸法線
    msp.add_linear_dim(
        base=(W / 2, -H * 0.08),
        p1=(0, 0), p2=(W, 0),
        dimstyle=cfg.dim_style,
        override={"dimtxt": H * 0.025}
    )

    # 縦寸法線
    msp.add_linear_dim(
        base=(-W * 0.08, H / 2),
        p1=(0, 0), p2=(0, H),
        angle=90,
        dimstyle=cfg.dim_style,
        override={"dimtxt": H * 0.025}
    )

    # グリッド線（設定ピッチ）
    grid_interval = cfg.elevation_grid
    x_pos = grid_interval
    while x_pos < W:
        msp.add_line((x_pos, 0), (x_pos, H),
                     dxfattribs={"color": cfg.grid_color, "linetype": "DASHED"})
        msp.add_text(f"{x_pos:.0f}m",
                     dxfattribs={"height": H * 0.02, "color": cfg.grid_color,
                                 "insert": (x_pos, -H * 0.04)})
        x_pos += grid_interval

    y_pos = grid_interval
    while y_pos < H:
        msp.add_line((0, y_pos), (W, y_pos),
                     dxfattribs={"color": cfg.grid_color, "linetype": "DASHED"})
        msp.add_text(f"{y_pos:.0f}m",
                     dxfattribs={"height": H * 0.02, "color": cfg.grid_color,
                                 "insert": (-W * 0.06, y_pos)})
        y_pos += grid_interval

    doc.saveas(output_path)
    return output_path


# =====================
# 展開図の天端線・地盤線抽出
# =====================
def extract_face_outline(img: np.ndarray, meta: dict, cfg: Config = None):
    """
    展開図のラスタ画像から、各列の最上点(天端)・最下点(地盤)を抽出する。
    戻り値：(crown, ground) いずれも [(h_rel, v_rel), ...]（DXF相対座標 0..W, 0..H）
    """
    cfg = _cfg(cfg)
    h_px, w_px = img.shape[0], img.shape[1]
    W = meta.get("width_m", 1.0)
    H = meta.get("height_m", 1.0)
    bg = np.array([int(c * 255) for c in cfg.bg])
    diff = np.abs(img.astype(int) - bg).sum(axis=2)
    fg = diff > 40  # 背景から十分離れた画素＝点群あり
    crown, ground = [], []
    for x in range(w_px):
        rows = np.where(fg[:, x])[0]
        if len(rows) == 0:
            continue
        hx = (x + 0.5) / w_px * W
        crown.append((hx, H - (rows.min() + 0.5) / h_px * H))   # 上端
        ground.append((hx, H - (rows.max() + 0.5) / h_px * H))  # 下端
    return crown, ground


# =====================
# 表題欄
# =====================
def _draw_title_block(msp, W, H, title, cfg):
    """図面右下に表題欄（工事名・図面名・縮尺・作成者・作成日）を描く。"""
    import datetime
    bw, bh = W * 0.34, H * 0.26
    x0 = W - bw
    y0 = -H * 0.20 - bh  # 図面の下に配置
    rows = [
        ("工事名", cfg.project_name or "―"),
        ("図面名", title),
        ("縮尺", cfg.drawing_scale),
        ("作成者", cfg.author),
        ("作成日", datetime.date.today().isoformat()),
    ]
    n = len(rows)
    rh = bh / n
    col = x0 + bw * 0.30
    th = rh * 0.45
    msp.add_lwpolyline(
        [(x0, y0), (x0 + bw, y0), (x0 + bw, y0 + bh), (x0, y0 + bh), (x0, y0)],
        dxfattribs={"layer": "TBLOCK", "color": cfg.frame_color, "lineweight": 50})
    msp.add_line((col, y0), (col, y0 + bh),
                 dxfattribs={"layer": "TBLOCK", "color": cfg.frame_color})
    for i, (k, v) in enumerate(rows):
        yy = y0 + bh - (i + 1) * rh
        if i > 0:
            msp.add_line((x0, yy + rh), (x0 + bw, yy + rh),
                         dxfattribs={"layer": "TBLOCK", "color": cfg.frame_color})
        msp.add_text(k, dxfattribs={"layer": "TBLOCK", "height": th,
                                    "color": cfg.title_color,
                                    "insert": (x0 + bw * 0.02, yy + rh * 0.3)})
        msp.add_text(str(v), dxfattribs={"layer": "TBLOCK", "height": th,
                                         "color": cfg.title_color,
                                         "insert": (col + bw * 0.02, yy + rh * 0.3)})


# =====================
# DXF出力（擁壁展開図：測点・標高軸・天端/地盤線・表題欄）
# =====================
def export_developed_elevation_dxf(img: np.ndarray, meta: dict, output_path: str,
                                   title: str = "擁壁展開図", cfg: Config = None,
                                   image_path: str = None):
    """
    測量様式に沿った擁壁展開図をDXF出力する。
    - 横軸：測点(No.)＋追加距離（展開長）
    - 縦軸：実標高(m)
    - 天端線・地盤線（点群画像から抽出）
    - スキャン画像の実寸埋め込み、表題欄、縮尺表記
    """
    import math
    cfg = _cfg(cfg)
    doc = ezdxf.new(dxfversion=cfg.dxf_version)
    doc.units = 6
    msp = doc.modelspace()
    for name, color in (("FRAME", cfg.frame_color), ("GRID", cfg.grid_color),
                        ("AXIS", cfg.grid_color), ("STATION", cfg.grid_color),
                        ("CROWN", cfg.crown_line_color), ("GROUND", cfg.ground_line_color),
                        ("TITLE", cfg.title_color), ("TBLOCK", cfg.frame_color)):
        doc.layers.new(name=name, dxfattribs={"color": color})

    W = meta.get("width_m", 1.0)
    H = meta.get("height_m", 1.0)
    v_min = meta.get("v_min", 0.0)
    v_max = meta.get("v_max", v_min + H)
    v_span = (v_max - v_min) or 1.0

    # スキャン画像を実寸で埋め込み
    _embed_raster(doc, msp, img, output_path, W, H, cfg, image_path)

    # 外枠
    msp.add_lwpolyline([(0, 0), (W, 0), (W, H), (0, H), (0, 0)],
                       dxfattribs={"layer": "FRAME", "color": cfg.frame_color, "lineweight": 50})

    # 標高軸（左・実標高ラベル＋横グリッド）
    ep = cfg.elevation_axis_pitch
    e = math.ceil(v_min / ep) * ep
    while e <= v_max + 1e-6:
        y = (e - v_min) / v_span * H
        msp.add_line((0, y), (W, y),
                     dxfattribs={"layer": "GRID", "color": cfg.grid_color, "linetype": "DASHED"})
        msp.add_text(f"{e:.1f}", dxfattribs={"layer": "AXIS", "height": H * 0.025,
                                             "color": cfg.grid_color,
                                             "insert": (-W * 0.05, y - H * 0.012)})
        e += ep
    msp.add_text("標高(m)", dxfattribs={"layer": "AXIS", "height": H * 0.03,
                                       "color": cfg.title_color}
                 ).set_placement((-W * 0.09, H * 0.5),
                                 align=TextEntityAlignment.MIDDLE_CENTER)

    # 測点軸（下・No.＋追加距離＋縦グリッド）
    sp = cfg.station_pitch
    no = cfg.station_zero_no
    d = 0.0
    while d <= W + 1e-6:
        msp.add_line((d, 0), (d, H),
                     dxfattribs={"layer": "STATION", "color": cfg.grid_color, "linetype": "DASHED"})
        msp.add_text(f"No.{no}", dxfattribs={"layer": "STATION", "height": H * 0.028,
                                             "color": cfg.grid_color}
                     ).set_placement((d, -H * 0.06), align=TextEntityAlignment.MIDDLE_CENTER)
        msp.add_text(f"{d:.1f}m", dxfattribs={"layer": "STATION", "height": H * 0.022,
                                              "color": cfg.grid_color}
                     ).set_placement((d, -H * 0.11), align=TextEntityAlignment.MIDDLE_CENTER)
        d += sp
        no += 1
    msp.add_text("測点", dxfattribs={"layer": "STATION", "height": H * 0.03,
                                    "color": cfg.title_color}
                 ).set_placement((W * 0.5, -H * 0.17),
                                 align=TextEntityAlignment.MIDDLE_CENTER)

    # 天端線・地盤線
    if cfg.extract_outline and img is not None:
        crown, ground = extract_face_outline(img, meta, cfg)
        if len(crown) >= 2:
            msp.add_lwpolyline(crown, dxfattribs={"layer": "CROWN",
                                                  "color": cfg.crown_line_color, "lineweight": 40})
        if len(ground) >= 2:
            msp.add_lwpolyline(ground, dxfattribs={"layer": "GROUND",
                                                   "color": cfg.ground_line_color, "lineweight": 40})

    # タイトル・縮尺
    msp.add_text(title, dxfattribs={"layer": "TITLE", "height": H * 0.05,
                                    "color": cfg.title_color}
                 ).set_placement((W / 2, H + H * 0.08),
                                 align=TextEntityAlignment.MIDDLE_CENTER)
    msp.add_text(f"縮尺 {cfg.drawing_scale}",
                 dxfattribs={"layer": "TITLE", "height": H * 0.03, "color": cfg.title_color,
                             "insert": (0, H + H * 0.03)})

    # 表題欄
    if cfg.draw_title_block:
        _draw_title_block(msp, W, H, title, cfg)

    doc.saveas(output_path)
    return output_path



# =====================
# DXF出力（平面図）
# =====================
def export_plan_dxf(pts: dict, meta: dict, output_path: str,
                    title: str = "平面図", cfg: Config = None,
                    img: np.ndarray = None, image_path: str = None):
    """
    平面図をDXFとして出力（グリッド＋点群 または ラスタ埋め込み）
    img を渡すとスキャン画像を実寸で埋め込み、重い点群プロットは省略する。
    """
    cfg = _cfg(cfg)
    doc = ezdxf.new(dxfversion=cfg.dxf_version)
    doc.units = 6
    msp = doc.modelspace()

    W = meta["width_m"]
    H = meta["height_m"]
    x_min = meta["x_min"]
    y_min = meta["y_min"]

    # スキャン画像を実寸で埋め込み（枠・線より下に敷く）
    embedded = _embed_raster(doc, msp, img, output_path, W, H, cfg, image_path)

    # 外枠
    msp.add_lwpolyline(
        [(0, 0), (W, 0), (W, H), (0, H), (0, 0)],
        dxfattribs={"color": cfg.frame_color, "lineweight": 50}
    )

    # タイトル
    msp.add_text(
        title,
        dxfattribs={"height": max(W, H) * 0.025, "color": cfg.title_color}
    ).set_placement((W / 2, H + max(W, H) * 0.04), align=TextEntityAlignment.MIDDLE_CENTER)

    # グリッド（設定ピッチ）
    grid = cfg.plan_grid
    xg = grid
    while xg < W:
        msp.add_line((xg, 0), (xg, H), dxfattribs={"color": cfg.grid_color, "linetype": "DASHED"})
        msp.add_text(f"{x_min + xg:.1f}",
                     dxfattribs={"height": max(W, H) * 0.015, "color": cfg.grid_color,
                                 "insert": (xg, -max(W, H) * 0.03)})
        xg += grid

    yg = grid
    while yg < H:
        msp.add_line((0, yg), (W, yg), dxfattribs={"color": cfg.grid_color, "linetype": "DASHED"})
        msp.add_text(f"{y_min + yg:.1f}",
                     dxfattribs={"height": max(W, H) * 0.015, "color": cfg.grid_color,
                                 "insert": (-max(W, H) * 0.04, yg)})
        yg += grid

    # 画像を埋め込んだ場合は重い点群プロットを省略
    if not embedded:
        # 点群を間引いて点としてプロット（AutoCAD表示用）
        step = max(1, len(pts["x"]) // cfg.plan_max_plot_points)
        xs = pts["x"][::step] - x_min
        ys = pts["y"][::step] - y_min

        for i in range(len(xs)):
            msp.add_point((float(xs[i]), float(ys[i])),
                          dxfattribs={"color": cfg.point_color})

    doc.saveas(output_path)
    return output_path

# =====================
# センターライン取得
# 優先順位：中心線座標ファイル → 展開図センター → 点群自動推定
# =====================
def load_centerline_from_file(filepath: str) -> np.ndarray:
    """
    中心線座標ファイルを読み込む
    対応形式：CSV（x,y,z列）またはTXT（スペース区切り）
    戻り値：shape(N,3) のndarrayまたはNone
    """
    try:
        import csv
        path = Path(filepath)
        if not path.exists():
            return None
        with open(filepath, "r", encoding="utf-8-sig") as f:
            first = f.read(1024)
        delimiter = "," if "," in first else None
        data = np.loadtxt(filepath, delimiter=delimiter, comments="#")
        if data.ndim == 1:
            data = data.reshape(1, -1)
        if data.shape[1] >= 3:
            return data[:, :3]
        elif data.shape[1] == 2:
            # X,Zのみの場合はY=0で補完
            return np.column_stack([data[:, 0],
                                    np.zeros(len(data)),
                                    data[:, 1]])
        return None
    except Exception:
        return None


def estimate_centerline_from_pts(pts: dict, n_slices: int = 50) -> np.ndarray:
    """
    点群から中心線を自動推定
    Z方向をn_slicesに分割し各スライスのXY重心を繋ぐ
    戻り値：shape(N,3) のndarray
    """
    x, y, z = pts["x"], pts["y"], pts["z"]
    z_min, z_max = z.min(), z.max()
    centers = []
    for i in range(n_slices):
        z0 = z_min + (z_max - z_min) * i / n_slices
        z1 = z_min + (z_max - z_min) * (i + 1) / n_slices
        mask = (z >= z0) & (z < z1)
        if mask.sum() < 10:
            continue
        cx = x[mask].mean()
        cy = y[mask].mean()
        cz = (z0 + z1) / 2
        centers.append([cx, cy, cz])
    return np.array(centers) if centers else None


def get_centerline(pts: dict,
                   centerline_file: str = None,
                   elevation_meta: dict = None) -> np.ndarray:
    """
    センターライン取得（優先順位付き）
    1. 中心線座標ファイル
    2. 展開図メタデータのセンター
    3. 点群から自動推定
    """
    # 優先1：外部ファイル
    if centerline_file:
        cl = load_centerline_from_file(centerline_file)
        if cl is not None:
            return cl, "centerline_file"

    # 優先2：展開図センター（arc方向の中央をセンターとして使用）
    if elevation_meta and "arc_min" in elevation_meta:
        arc_center = (elevation_meta["arc_min"] + elevation_meta["arc_max"]) / 2
        z_min = elevation_meta["z_min"]
        z_max = elevation_meta["z_max"]
        n = 50
        cl = np.array([
            [arc_center, 0, z_min + (z_max - z_min) * i / n]
            for i in range(n + 1)
        ])
        return cl, "elevation_center"

    # 優先3：点群から自動推定
    cl = estimate_centerline_from_pts(pts)
    return cl, "auto_estimated"


# =====================
# 縦断図生成
# =====================
def _robust_extreme(arr: np.ndarray, direction: str, pct: float = 3.0) -> float:
    """
    ノイズ点1個に引っ張られない極値推定。
    生の min/max は点群中の飛び点（スキャンノイズ）1つだけで大きく跳ね、
    縦断図がギザギザ（がたがた）になる原因になる。上位/下位 pct% の点の
    平均を取ることで、外れ値の影響を抑えつつ実際の縁の高さに近い値を返す。
    """
    if arr.size == 0:
        return 0.0
    if pct <= 0:
        return float(arr.max() if direction == "max" else arr.min())
    if direction == "max":
        thresh = np.percentile(arr, 100.0 - pct)
        sel = arr[arr >= thresh]
    else:
        thresh = np.percentile(arr, pct)
        sel = arr[arr <= thresh]
    if sel.size == 0:
        return float(arr.max() if direction == "max" else arr.min())
    return float(sel.mean())


def _smooth_series(points: list, window: int = 5) -> list:
    """
    縦断ラインを移動中央値で平滑化する（距離はそのまま、高さのみ均す）。
    移動平均だと単発ノイズも複数スライスに渡る実際の凹凸も一律にぼかしてしまう。
    移動中央値なら、window//2+1 スライス以上続かない単発の飛び値（ノイズ）だけ
    消え、実際の構造物による凹凸（隣接スライスにまたがって連続する変化）は
    形をほぼ保ったまま残る。
    window<=1 または点数が足りない場合はそのまま返す。
    """
    if window <= 1 or len(points) < window:
        return points
    dists = [p[0] for p in points]
    vals = np.asarray([p[1] for p in points], dtype=float)
    pad = window // 2
    padded = np.pad(vals, pad, mode="edge")
    smoothed = np.array([
        np.median(padded[i:i + window]) for i in range(len(vals))
    ])
    return list(zip(dists, smoothed.tolist()))


def _ring_coverage(dx: np.ndarray, dy: np.ndarray, sectors: int = 12) -> float:
    """
    断面内の点が中心周りをどれだけ取り囲んでいるか(0〜1)。
    坑口の外（地形・植生などの開いた区間）は点が偏り、カバレッジが低く出る。
    トンネル内部（閉じた管）はカバレッジがほぼ1になる。
    """
    if dx.size == 0:
        return 0.0
    ang = np.arctan2(dy, dx)
    sec = (np.mod(ang, 2 * np.pi) / (2 * np.pi) * sectors).astype(int) % sectors
    return len(np.unique(sec)) / sectors


def _longest_covered_range(dists: list, coverage: list, min_cover: float):
    """
    coverage が min_cover 以上連続する最長区間の (開始距離, 終了距離) を返す。
    見つからない、または区間が全体の大部分（97%以上）を占め区別する意味が
    ない場合、あるいは逆に短すぎて検出が怪しい（15%未満）場合は None。
    """
    if not dists:
        return None
    valid = [c >= min_cover for c in coverage]
    best = None
    i, n = 0, len(valid)
    while i < n:
        if valid[i]:
            j = i
            while j < n and valid[j]:
                j += 1
            length = dists[j - 1] - dists[i]
            if best is None or length > (dists[best[1]] - dists[best[0]]):
                best = (i, j - 1)
            i = j
        else:
            i += 1
    if best is None:
        return None
    t0, t1 = dists[best[0]], dists[best[1]]
    total = dists[-1] - dists[0]
    if total <= 0:
        return None
    frac = (t1 - t0) / total
    if frac >= 0.97 or frac < 0.15:
        return None
    return (t0, t1)


def generate_profile(pts: dict,
                     centerline: np.ndarray,
                     slice_width: float = None,
                     n_slices: int = None,
                     tunnel_axis: str = None,
                     swap_lr: bool = False,
                     cfg: Config = None) -> dict:
    """
    トンネル縦断図データを生成

    tunnel_axis="z"  : Z軸がトンネル延長方向（RS10/Scaniverse標準）
    tunnel_axis="xy" : XY平面がトンネル延長、センターライン追従
    各引数Noneのときは設定値
    (profile_slice_width / profile_n_slices / profile_tunnel_axis) を使用
    swap_lr : True で左右を入れ替える（センターラインの向きが実際の上流→下流と
              逆だった場合の補正用。GUIの「左右ミラー」に対応）

    戻り値：天端・左壁・右壁・地面・センターの（延長距離, 高さ）リスト
    """
    cfg = _cfg(cfg)
    if slice_width is None:
        slice_width = cfg.profile_slice_width
    if n_slices is None:
        n_slices = cfg.profile_n_slices
    if tunnel_axis is None:
        tunnel_axis = cfg.profile_tunnel_axis
    x, y, z = pts["x"], pts["y"], pts["z"]

    profile = {
        "crown": [], "left": [], "right": [], "floor": [], "center": [],
    }

    slice_dists = []
    slice_cov = []

    if tunnel_axis == "z":
        # Z軸がトンネル延長（横断面 = XY平面）
        z_min, z_max = z.min(), z.max()
        total_length = z_max - z_min
        slice_step = total_length / n_slices

        for i in range(n_slices):
            z0 = z_min + slice_step * i
            z1 = z_min + slice_step * (i + 1)
            mask = (z >= z0) & (z < z1)
            if mask.sum() < 10:
                continue

            dist = (z0 - z_min) + slice_step * 0.5
            xi = x[mask]
            yi = y[mask]
            cx = xi.mean()
            cy = yi.mean()

            # 天端=Y最大、地面=Y最小、左壁=X最小側Y平均、右壁=X最大側Y平均
            profile["crown"].append((dist, _robust_extreme(yi, "max", cfg.profile_outlier_pct)))
            profile["floor"].append((dist, _robust_extreme(yi, "min", cfg.profile_outlier_pct)))
            profile["center"].append((dist, float(cy)))

            left_mask = xi < cx
            right_mask = xi >= cx
            if left_mask.sum() > 2:
                profile["left"].append((dist, float(yi[left_mask].mean())))
            if right_mask.sum() > 2:
                profile["right"].append((dist, float(yi[right_mask].mean())))

            slice_dists.append(dist)
            slice_cov.append(_ring_coverage(xi - cx, yi - cy, cfg.profile_portal_sectors))

    else:
        # XY平面延長・センターライン追従モード
        if len(centerline) < 2:
            return None

        diffs = np.diff(centerline[:, :2], axis=0)
        seg_lengths = np.maximum(np.sqrt((diffs**2).sum(axis=1)), 1e-9)
        cum_dist = np.concatenate([[0], np.cumsum(seg_lengths)])
        total_length = cum_dist[-1]
        slice_step = total_length / n_slices

        for i in range(n_slices):
            dist = slice_step * (i + 0.5)
            idx = int(np.searchsorted(cum_dist, dist))
            idx = np.clip(idx, 1, len(centerline) - 1)
            t = (dist - cum_dist[idx-1]) / seg_lengths[idx-1]
            cx = centerline[idx-1, 0] + t*(centerline[idx, 0]-centerline[idx-1, 0])
            cy = centerline[idx-1, 1] + t*(centerline[idx, 1]-centerline[idx-1, 1])

            dx = x - cx
            dy = y - cy
            mask = np.sqrt(dx**2 + dy**2) < slice_width
            if mask.sum() < 5:
                continue

            z_sl = z[mask]
            dx_sl = dx[mask]
            dy_sl = dy[mask]

            # センターラインは上流→下流の順を前提とする。進行方向（接線）を
            # 90°左回転したベクトルが「下流を向いて左」＝河川の左岸方向。
            seg_tx = centerline[idx, 0] - centerline[idx-1, 0]
            seg_ty = centerline[idx, 1] - centerline[idx-1, 1]
            seg_len = max(np.hypot(seg_tx, seg_ty), 1e-9)
            seg_tx /= seg_len
            seg_ty /= seg_len
            left_x, left_y = -seg_ty, seg_tx

            # 点の法線方向オフセット（dx・dy 両方を使う。片方だけだと通路の向き次第で
            # 左右が入れ替わり、左右の擁壁が混ざって表示される原因になっていた）
            side = dx_sl * left_x + dy_sl * left_y
            lm = side > 0   # 左岸（下流向き基準）
            rm = side <= 0  # 右岸

            profile["crown"].append((dist, _robust_extreme(z_sl, "max", cfg.profile_outlier_pct)))
            profile["floor"].append((dist, _robust_extreme(z_sl, "min", cfg.profile_outlier_pct)))
            profile["center"].append((dist, float(z_sl.mean())))
            if lm.sum() > 2:
                profile["left"].append((dist, float(np.median(z_sl[lm]))))
            if rm.sum() > 2:
                profile["right"].append((dist, float(np.median(z_sl[rm]))))

            slice_dists.append(dist)
            slice_cov.append(_ring_coverage(dx_sl, dy_sl, cfg.profile_portal_sectors))

    if swap_lr:
        profile["left"], profile["right"] = profile["right"], profile["left"]

    if cfg.profile_smooth_window > 1:
        for key in profile:
            profile[key] = _smooth_series(profile[key], cfg.profile_smooth_window)

    profile["tunnel_range"] = None
    if cfg.profile_show_tunnel_extent:
        profile["tunnel_range"] = _longest_covered_range(
            slice_dists, slice_cov, cfg.profile_portal_min_cover)

    return profile


# =====================
# 縦断図 DXF出力
# =====================
PROFILE_LINE_KEYS = ["crown", "left", "right", "floor", "center"]
PROFILE_LINE_LABELS = {"crown": "天端", "left": "左壁", "right": "右壁",
                        "floor": "地面", "center": "センター"}


def export_profile_dxf(profile: dict, output_path: str,
                        title: str = "縦断図",
                        v_scale: float = None,
                        cfg: Config = None,
                        only: str = None) -> str:
    """
    縦断図をDXFとして出力

    レイヤー：
    - CROWN   : 天端ライン
    - LEFT    : 左壁ライン
    - RIGHT   : 右壁ライン
    - FLOOR   : 地面ライン
    - CENTER  : センターライン
    - GRID    : グリッド・寸法
    - TITLE   : タイトル・凡例

    色・グリッドピッチ・縦スケールは設定（config）で変更可能。
    v_scale Noneのときは設定値(profile_v_scale)を使用。
    only : "crown"/"left"/"right"/"floor"/"center" のいずれかを指定すると、
           そのラインだけを描いた単体DXFを出力する（他4本は描かない）。
           Noneのときは従来通り5本すべてをまとめて描く。
    """
    cfg = _cfg(cfg)
    if v_scale is None:
        v_scale = cfg.profile_v_scale
    line_keys = [only] if only else PROFILE_LINE_KEYS
    doc = ezdxf.new(dxfversion=cfg.dxf_version)
    doc.units = 6  # meters
    msp = doc.modelspace()

    # レイヤー定義（色は設定から）
    layers = {
        "CROWN":  {"color": cfg.crown_color},
        "LEFT":   {"color": cfg.left_color},
        "RIGHT":  {"color": cfg.right_color},
        "FLOOR":  {"color": cfg.floor_color},
        "CENTER": {"color": cfg.center_color},
        "GRID":   {"color": cfg.grid_color},
        "TITLE":  {"color": cfg.title_color},
    }
    for name, attr in layers.items():
        doc.layers.new(name=name, dxfattribs=attr)

    # 描画対象ラインのみでスケール計算
    all_dists = []
    all_zs = []
    for key in line_keys:
        for d, z in profile.get(key, []):
            all_dists.append(d)
            all_zs.append(z)

    if not all_dists:
        return output_path

    d_min = min(all_dists)
    d_max = max(all_dists)
    z_min = min(all_zs)
    z_max = max(all_zs)
    total_len = d_max - d_min
    z_range = max(z_max - z_min, 0.1)

    # 縦断図の縦横比調整（縦スケール）
    effective_v_scale = v_scale

    def to_dxf(dist, z):
        """実座標→DXF座標変換"""
        return (dist - d_min, (z - z_min) * effective_v_scale)

    # ラインデータ描画
    line_configs = [
        ("crown",  "CROWN",  cfg.crown_color,  "天端"),
        ("left",   "LEFT",   cfg.left_color,   "左壁"),
        ("right",  "RIGHT",  cfg.right_color,  "右壁"),
        ("floor",  "FLOOR",  cfg.floor_color,  "地面"),
        ("center", "CENTER", cfg.center_color, "センター"),
    ]
    line_configs = [c for c in line_configs if c[0] in line_keys]

    # トンネル区間（閉じた管）の外側＝坑口や地形クラッターの影響が疑われる区間は、
    # 細線＋破線にして「参考値」であることを示す。区間内は通常通り太い実線。
    tunnel_range = profile.get("tunnel_range")

    for key, layer, color, label in line_configs:
        pts_line = profile.get(key, [])
        if len(pts_line) < 2:
            continue
        if tunnel_range and key != "center":
            t0, t1 = tunnel_range
            before = [p for p in pts_line if p[0] < t0]
            inside = [p for p in pts_line if t0 <= p[0] <= t1]
            after = [p for p in pts_line if p[0] > t1]
            if before and inside:
                before = before + [inside[0]]
            if inside and after:
                inside = inside + [after[0]]
            for seg, is_outside in ((before, True), (inside, False), (after, True)):
                if len(seg) < 2:
                    continue
                coords = [to_dxf(d, zz) for d, zz in seg]
                attrs = {"layer": layer, "color": color,
                         "lineweight": 15 if is_outside else 30}
                if is_outside:
                    attrs["linetype"] = "DASHED"
                msp.add_lwpolyline(coords, dxfattribs=attrs)
        else:
            coords = [to_dxf(d, zz) for d, zz in pts_line]
            msp.add_lwpolyline(
                coords,
                dxfattribs={"layer": layer, "color": color, "lineweight": 30}
            )

    # グリッド（横：延長に応じてピッチ切替）
    grid_h = (cfg.profile_grid_h_short
              if total_len < cfg.profile_grid_h_threshold
              else cfg.profile_grid_h_long)
    gd = grid_h
    while gd <= total_len:
        x_g = gd - d_min
        y_top = z_range * effective_v_scale
        msp.add_line(
            (x_g, 0), (x_g, y_top),
            dxfattribs={"layer": "GRID", "color": cfg.grid_color, "linetype": "DASHED"}
        )
        msp.add_text(
            f"{gd:.0f}m",
            dxfattribs={"layer": "GRID", "height": z_range * effective_v_scale * 0.04,
                        "color": cfg.grid_color, "insert": (x_g, -z_range * effective_v_scale * 0.06)}
        )
        gd += grid_h

    # グリッド（縦：設定ピッチ）
    grid_v = cfg.profile_grid_v
    gz = grid_v
    while gz <= z_range:
        y_g = gz * effective_v_scale
        msp.add_line(
            (0, y_g), (total_len, y_g),
            dxfattribs={"layer": "GRID", "color": cfg.grid_color, "linetype": "DASHED"}
        )
        msp.add_text(
            f"{z_min + gz:.1f}m",
            dxfattribs={"layer": "GRID",
                        "height": z_range * effective_v_scale * 0.04,
                        "color": cfg.grid_color,
                        "insert": (-total_len * 0.06, y_g)}
        )
        gz += grid_v

    # 外枠
    msp.add_lwpolyline(
        [(0, 0), (total_len, 0),
         (total_len, z_range * effective_v_scale),
         (0, z_range * effective_v_scale), (0, 0)],
        dxfattribs={"layer": "GRID", "color": cfg.frame_color, "lineweight": 50}
    )

    # トンネル区間の始点・終点マーカー（どこで坑口になっているかを明示）
    if tunnel_range:
        t0, t1 = tunnel_range
        top_y = z_range * effective_v_scale
        mark_h = z_range * effective_v_scale * 0.035
        for td, mlabel in ((t0, "▽坑口(始点)"), (t1, "▽坑口(終点)")):
            xg = td - d_min
            msp.add_line(
                (xg, 0), (xg, top_y),
                dxfattribs={"layer": "GRID", "color": cfg.frame_color,
                            "lineweight": 70, "linetype": "DASHED"}
            )
            msp.add_text(
                mlabel,
                dxfattribs={"layer": "GRID", "height": mark_h, "color": cfg.frame_color,
                            "insert": (xg, top_y + mark_h * 0.3)}
            )

    # タイトル
    title_h = z_range * effective_v_scale * 0.05
    msp.add_text(
        title,
        dxfattribs={"layer": "TITLE", "height": title_h, "color": cfg.title_color,
                    "insert": (total_len / 2, z_range * effective_v_scale + title_h * 1.5)}
    )

    # 凡例
    legend_x = total_len * 1.02
    legend_y = z_range * effective_v_scale
    leg_h = z_range * effective_v_scale * 0.04
    leg_items = [(PROFILE_LINE_LABELS[key], color)
                 for key, _, color, _ in line_configs]
    for j, (label, color) in enumerate(leg_items):
        y_pos = legend_y - j * leg_h * 2
        msp.add_line(
            (legend_x, y_pos),
            (legend_x + total_len * 0.05, y_pos),
            dxfattribs={"color": color, "lineweight": 30}
        )
        msp.add_text(
            label,
            dxfattribs={"height": leg_h, "color": color,
                        "insert": (legend_x + total_len * 0.06, y_pos - leg_h * 0.3)}
        )

    # 縦スケール注記
    msp.add_text(
        f"縦スケール×{effective_v_scale:.0f}" if effective_v_scale != 1.0 else "縦スケール×1（実寸）",
        dxfattribs={"layer": "TITLE", "height": leg_h, "color": cfg.grid_color,
                    "insert": (0, -z_range * effective_v_scale * 0.12)}
    )

    if tunnel_range:
        msp.add_text(
            "破線＝坑口の外・地形クラッターの疑いがある区間（参考値）",
            dxfattribs={"layer": "TITLE", "height": leg_h * 0.9, "color": cfg.grid_color,
                        "insert": (0, -z_range * effective_v_scale * 0.18)}
        )

    doc.saveas(output_path)
    return output_path


# =====================
# 横断図生成
# =====================
def _cross_axes(axis: str):
    """延長軸から断面平面の2軸(横h, 縦v)を返す。"""
    return {
        "z": ("x", "y"),   # トンネル標準：延長Z、断面=XY平面
        "x": ("y", "z"),   # 擁壁がX方向に延びる：断面=YZ平面
        "y": ("x", "z"),   # 擁壁がY方向に延びる：断面=XZ平面
    }[axis]


def generate_cross_section(pts: dict, station: float,
                           mode: str = "tunnel", axis: str = None,
                           thickness: float = None, center: tuple = None,
                           cfg: Config = None) -> dict:
    """
    指定測点での横断面データを生成

    mode="tunnel" : 延長軸に直交する断面をリング状（閉輪郭）に抽出
    mode="wall"   : 壁面プロファイル（開輪郭）として抽出
    station       : 延長軸方向の切り出し位置
    axis          : 延長軸 ('z'/'x'/'y')。Noneなら設定値(cross_section_axis)
    thickness     : スライス厚み(m)。Noneなら設定値
    center        : トンネル中心(h,v)。Noneなら断面重心を使用

    戻り値：outline（断面平面上の座標リスト）とメタデータの辞書
    """
    cfg = _cfg(cfg)
    if axis is None:
        axis = cfg.cross_section_axis
    if thickness is None:
        thickness = cfg.cross_section_thickness

    h_name, v_name = _cross_axes(axis)
    ext = pts[axis]
    mask = np.abs(ext - station) < thickness / 2.0
    h = pts[h_name][mask]
    v = pts[v_name][mask]

    base = {
        "mode": mode, "station": float(station), "axis": axis,
        "h_label": h_name.upper(), "v_label": v_name.upper(),
        "n_points": int(mask.sum()),
    }

    if mask.sum() < 5:
        base.update({"outline": [], "closed": False,
                     "width_m": 0.0, "height_m": 0.0})
        return base

    if mode == "tunnel":
        if center is None:
            ch, cv = float(h.mean()), float(v.mean())
        else:
            ch, cv = center
        ang = np.arctan2(v - cv, h - ch)
        rad = np.sqrt((h - ch) ** 2 + (v - cv) ** 2)
        n = max(cfg.cross_section_n_angles, 8)
        bins = np.linspace(-np.pi, np.pi, n + 1)
        idx = np.clip(np.digitize(ang, bins) - 1, 0, n - 1)
        outline = []
        for b in range(n):
            sel = idx == b
            if sel.sum() < 1:
                continue
            r = float(np.median(rad[sel]))
            a = (bins[b] + bins[b + 1]) / 2.0
            outline.append((ch + r * np.cos(a), cv + r * np.sin(a)))
        if outline:
            outline.append(outline[0])  # 閉じる
        closed = True
        base["center"] = (ch, cv)
    else:  # wall
        n = max(cfg.cross_section_n_vbins, 4)
        v_min, v_max = float(v.min()), float(v.max())
        bins = np.linspace(v_min, v_max, n + 1)
        idx = np.clip(np.digitize(v, bins) - 1, 0, n - 1)
        outline = []
        for b in range(n):
            sel = idx == b
            if sel.sum() < 1:
                continue
            hmed = float(np.median(h[sel]))
            vc = (bins[b] + bins[b + 1]) / 2.0
            outline.append((hmed, vc))
        closed = False

    if outline:
        hs = [p[0] for p in outline]
        vs = [p[1] for p in outline]
        base.update({
            "h_min": min(hs), "h_max": max(hs),
            "v_min": min(vs), "v_max": max(vs),
            "width_m": max(hs) - min(hs),
            "height_m": max(vs) - min(vs),
        })
    else:
        base.update({"width_m": 0.0, "height_m": 0.0})

    base["outline"] = outline
    base["closed"] = closed
    return base


def generate_cross_sections(pts: dict, mode: str = "tunnel", axis: str = None,
                            stations=None, pitch: float = None,
                            thickness: float = None, center: tuple = None,
                            cfg: Config = None) -> list:
    """
    複数測点の横断面をまとめて生成

    stations : 測点リスト。Noneなら延長範囲をpitch間隔で自動生成
    pitch    : 測点ピッチ(m)。Noneなら設定値(cross_section_pitch)
    戻り値：generate_cross_section の辞書のリスト
    """
    cfg = _cfg(cfg)
    if axis is None:
        axis = cfg.cross_section_axis
    if pitch is None:
        pitch = cfg.cross_section_pitch

    if stations is None:
        ext = pts[axis]
        e_min, e_max = float(ext.min()), float(ext.max())
        stations = list(np.arange(e_min + pitch / 2.0, e_max, pitch))

    sections = []
    for st in stations:
        sec = generate_cross_section(pts, st, mode=mode, axis=axis,
                                     thickness=thickness, center=center, cfg=cfg)
        if sec["outline"]:
            sections.append(sec)
    return sections


# =====================
# 横断図 DXF出力
# =====================
def _draw_cross_section(msp, section: dict, ox: float, oy: float, cfg: Config):
    """断面1枚をDXFに描画（原点(ox,oy)基準）。描画領域の(幅,高さ)を返す。"""
    outline = section["outline"]
    if not outline:
        return 0.0, 0.0
    h_min = section["h_min"]
    v_min = section["v_min"]
    W = section["width_m"]
    H = section["height_m"]

    coords = [(p[0] - h_min + ox, p[1] - v_min + oy) for p in outline]

    # 断面輪郭
    msp.add_lwpolyline(
        coords,
        dxfattribs={"layer": "SECTION", "color": cfg.cross_section_color,
                    "lineweight": 40, "closed": section["closed"]}
    )

    # 外枠
    msp.add_lwpolyline(
        [(ox, oy), (ox + W, oy), (ox + W, oy + H), (ox, oy + H), (ox, oy)],
        dxfattribs={"layer": "GRID", "color": cfg.frame_color, "lineweight": 50}
    )

    # グリッド
    g = cfg.cross_section_grid
    gx = g
    while gx < W:
        msp.add_line((ox + gx, oy), (ox + gx, oy + H),
                     dxfattribs={"layer": "GRID", "color": cfg.grid_color,
                                 "linetype": "DASHED"})
        gx += g
    gy = g
    while gy < H:
        msp.add_line((ox, oy + gy), (ox + W, oy + gy),
                     dxfattribs={"layer": "GRID", "color": cfg.grid_color,
                                 "linetype": "DASHED"})
        gy += g

    # 測点ラベル
    label_h = max(W, H) * 0.05
    msp.add_text(
        f"STA {section['station']:.2f}m",
        dxfattribs={"layer": "TITLE", "height": label_h, "color": cfg.title_color}
    ).set_placement((ox + W / 2, oy + H + label_h * 0.6),
                    align=TextEntityAlignment.MIDDLE_CENTER)
    return W, H


def export_cross_section_dxf(section: dict, output_path: str,
                             title: str = "横断図", cfg: Config = None) -> str:
    """単一の横断面をDXF出力"""
    cfg = _cfg(cfg)
    doc = ezdxf.new(dxfversion=cfg.dxf_version)
    doc.units = 6
    msp = doc.modelspace()
    for name, color in (("SECTION", cfg.cross_section_color),
                        ("GRID", cfg.grid_color),
                        ("TITLE", cfg.title_color)):
        doc.layers.new(name=name, dxfattribs={"color": color})

    W, H = _draw_cross_section(msp, section, 0.0, 0.0, cfg)

    # タイトル
    title_h = max(W, H, 1.0) * 0.07
    msp.add_text(
        f"{title}（{section['mode']}）",
        dxfattribs={"layer": "TITLE", "height": title_h, "color": cfg.title_color}
    ).set_placement((W / 2, H + title_h * 2.2), align=TextEntityAlignment.MIDDLE_CENTER)
    doc.saveas(output_path)
    return output_path


def export_cross_sections_dxf(sections: list, output_path: str,
                              title: str = "横断図", cols: int = None,
                              cfg: Config = None) -> str:
    """複数の横断面を一定間隔で並べて1枚のDXFに出力"""
    cfg = _cfg(cfg)
    if not sections:
        return output_path
    doc = ezdxf.new(dxfversion=cfg.dxf_version)
    doc.units = 6
    msp = doc.modelspace()
    for name, color in (("SECTION", cfg.cross_section_color),
                        ("GRID", cfg.grid_color),
                        ("TITLE", cfg.title_color)):
        doc.layers.new(name=name, dxfattribs={"color": color})

    max_w = max(s["width_m"] for s in sections)
    max_h = max(s["height_m"] for s in sections)
    gap_x = max_w * 0.4
    gap_y = max_h * 0.6
    if cols is None:
        cols = max(1, int(round(len(sections) ** 0.5)))

    for i, sec in enumerate(sections):
        r = i // cols
        c = i % cols
        ox = c * (max_w + gap_x)
        oy = -r * (max_h + gap_y)
        _draw_cross_section(msp, sec, ox, oy, cfg)

    # 全体タイトル
    title_h = max(max_w, max_h) * 0.08
    msp.add_text(
        f"{title}（{sections[0]['mode']} / 全{len(sections)}断面）",
        dxfattribs={"layer": "TITLE", "height": title_h, "color": cfg.title_color,
                    "insert": (0, max_h + title_h * 2.5)}
    )
    doc.saveas(output_path)
    return output_path


if __name__ == "__main__":
    print("LAS処理モジュール読み込みOK")
    print(f"  現在の設定: 展開図解像度={DEFAULT_CONFIG.elevation_resolution}m/px, "
          f"平面図グリッド={DEFAULT_CONFIG.plan_grid}m, "
          f"縦スケール×{DEFAULT_CONFIG.profile_v_scale}")
