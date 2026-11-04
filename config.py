"""
設定モジュール
--------------
las_processor.py の各種パラメータをまとめて管理する。

使い方：
    from config import Config

    # 1) デフォルト設定を使う
    cfg = Config()

    # 2) JSONファイルから読み込む（無ければデフォルト）
    cfg = Config.load("config.json")

    # 3) 一部だけ上書きする
    cfg = Config(elevation_resolution=0.005, plan_grid=10.0)

    # 4) 現在の設定をJSONに書き出す（テンプレート作成に便利）
    cfg.save("config.json")

config.json を直接編集すれば、コードを触らずに見た目・精度・出力を調整できる。
"""

from dataclasses import dataclass, field, asdict
from pathlib import Path
import json


@dataclass
class Config:
    # =====================
    # 解像度（1ピクセルあたりの距離 m）小さいほど高精細・重い
    # =====================
    elevation_resolution: float = 0.01   # 展開図（擁壁）
    tunnel_resolution: float = 0.01      # 展開図（トンネル）
    tunnel_seam: str = "crown"           # トンネル展開の切り口 "crown"=天井で切る(インバート中央/魚の開き) / "invert"=底で切る(天端中央)
    plan_resolution: float = 0.05        # 平面図

    # =====================
    # ラスタ画像の最大ピクセル（メモリ保護。超えると自動で粗くする）
    # =====================
    elevation_max_pix: int = 8000        # 展開図（擁壁・トンネル共通）
    plan_max_pix: int = 6000             # 平面図

    # =====================
    # 壁面切り出し
    # =====================
    wall_thickness: float = 0.3          # 切り出し厚み(m)

    # =====================
    # 背景色（点群が無い部分の塗り色）RGB 各0.0〜1.0
    # =====================
    background_color: list = field(default_factory=lambda: [0.05, 0.08, 0.15])

    # =====================
    # 隙間埋め（点が疎で生じる画素の穴を近傍色で補間。砂嵐対策）
    # =====================
    fill_gaps: bool = True               # 小さな空き画素を近傍の色で埋める
    fill_radius_px: int = 4              # 埋める最大距離(px)。大きいほど穴が消えるがにじむ

    # =====================
    # 展開図の仕上げ品質（つぶさず・きれいに・無駄なカット無し）
    # =====================
    dev_resolution: float = 0.005        # 展開図の目標解像度(m/px)。細かいほど高精細・重い
    dev_trim_radius: float = 0.0         # トンネル管トリム半径(m)。中心からこの距離より外（坑口/地形/植生）を除外。0=自動推定
    dev_uniform: bool = True             # True=均一角度展開(縦=角度×基準半径。非円形でも空白帯なくクリーン) / False=実半径展開(断面サイズ差をそのまま出す)
    dev_auto_trim_ends: bool = True      # 坑口など「開いた端」を自動除外し、閉じた管の区間だけ展開する
    dev_trim_ends_m: float = 0.0         # さらに延長方向の両端を手動で各◯m切る（坑口の乱れを微調整。0=切らない）
    dev_points_per_pixel: float = 0.5    # 1画素あたり目標点数。小さいほど高精細（穴は滑らか補間で埋める）
    dev_max_total_pix: int = 30000000    # 展開図ラスタ総ピクセル上限（縦つぶれ回避＋メモリ保護）
    dev_max_axis_pix: int = 32000        # 1軸あたりの画素上限
    dev_fill_radius_px: int = 6          # 展開図の穴埋め半径(px)。大きいほど穴が消えるが甘くなる
    dev_splat_radius: int = 1            # 点を小さな円盤(スプラット)で描く半径(px)。0=1画素(最も鮮明)。大きいほど滑らかだが甘くなる
    finish_denoise: bool = True          # 孤立した飛び点ノイズ画素のみ除去（構造はカットしない）
    finish_denoise_min_px: int = 3       # この画素数未満の孤立島を除去
    finish_levels: bool = True           # 色の自動レベル補正（暗い点群を見やすく）
    finish_levels_pct: float = 1.0       # 上下何%でクリップして引き伸ばすか

    # =====================
    # 左壁/右壁 個別展開図
    # =====================
    wall_split: str = "centerline"       # 左右の分割基準 "centerline"=中心鉛直面で二分 / "springline"=側壁のみ
    wall_datum: str = "invert"           # 縦軸の基準 "invert"=底基準 / "springline"=起拱線基準
    flip_lr: bool = False                # 進行方向の取り方で左右が逆になる場合に反転

    # =====================
    # グリッド線のピッチ(m)
    # =====================
    elevation_grid: float = 1.0          # 展開図：1mピッチ
    plan_grid: float = 5.0               # 平面図：5mピッチ
    profile_grid_h_short: float = 5.0    # 縦断図 横：延長が短いとき
    profile_grid_h_long: float = 10.0    # 縦断図 横：延長が長いとき
    profile_grid_h_threshold: float = 100.0  # 横グリッド切替の延長しきい値(m)
    profile_grid_v: float = 1.0          # 縦断図 縦：1mピッチ

    # =====================
    # 縦断図のパラメータ
    # =====================
    profile_n_slices: int = 100          # 延長方向の分割数（多いほど滑らか）
    profile_slice_width: float = 0.5     # XY追従モードのスライス幅(m)
    profile_v_scale: float = 1.0         # 縦スケール倍率（1=実寸、5で5倍強調）
    profile_tunnel_axis: str = "z"       # "z"=Z軸が延長 / "xy"=XY平面が延長
    profile_outlier_pct: float = 3.0     # 天端/地面の極値推定で使う上下◯%点の平均（飛び点1個での跳ねを抑える。0=生のmax/min）
    profile_smooth_window: int = 5       # 縦断ラインの移動中央値ウィンドウ（点数。ノイズは消し実際の凹凸は残す。0/1=平滑化なし）
    profile_show_tunnel_extent: bool = True  # トンネル区間（閉じた管）の始点/終点を自動検出して図に明示する
    profile_portal_min_cover: float = 0.5    # 断面の角度カバレッジがこれ未満なら坑口・地形クラッターとみなす
    profile_portal_sectors: int = 12         # 角度カバレッジ判定の分割数

    # =====================
    # 平面図の点群プロット
    # =====================
    plan_max_plot_points: int = 50000    # DXFに描く点の最大数（間引き基準）

    # =====================
    # 横断図のパラメータ
    # =====================
    cross_section_thickness: float = 0.2   # 断面スライス厚み(m)
    cross_section_axis: str = "z"          # 延長軸 トンネル既定="z" / 擁壁は"x"等
    cross_section_n_angles: int = 360      # トンネル断面の角度分割数（リング解像度）
    cross_section_n_vbins: int = 200       # 擁壁断面の高さ方向ビン数
    cross_section_pitch: float = 10.0      # 複数断面の測点ピッチ(m)
    cross_section_grid: float = 1.0        # 横断図のグリッドピッチ(m)
    cross_section_color: int = 1           # 断面輪郭線の色（既定=赤）

    # =====================
    # DXF全般
    # =====================
    dxf_version: str = "R2010"           # AutoCADバージョン
    dim_style: str = "EZ_M_100_H25_CM"   # 寸法スタイル名（ezdxf標準）
    embed_raster: bool = True            # 展開図/平面図のスキャン画像をDXFに実寸で埋め込む

    # =====================
    # 展開図（擁壁）の様式
    # =====================
    station_pitch: float = 5.0           # 測点ピッチ(m)（横軸の測点目盛）
    station_zero_no: int = 0             # 起点の測点番号（No.）
    elevation_axis_pitch: float = 1.0    # 標高軸（縦軸）の目盛ピッチ(m)
    extract_outline: bool = True         # 天端線・地盤線を点群画像から抽出して描く
    draw_title_block: bool = True        # 表題欄を描く
    drawing_scale: str = "1:100"         # 縮尺表記
    project_name: str = ""               # 工事名（表題欄）
    author: str = "岳_デザイン"          # 作成者（表題欄）
    crown_line_color: int = 1            # 天端線の色（赤）
    ground_line_color: int = 2           # 地盤線の色（黄）

    # =====================
    # 色（AutoCADカラーインデックス 1=赤 2=黄 3=緑 4=水 5=青 6=紫 7=白 8=灰）
    # 色7(白)はプレビューPNG（白背景）で見えなくなるため使用しない。
    # 縦断図の5本（天端/左壁/右壁/地面/センター）は同時に表示されるため
    # 互いに紛れない色を割り当てている。
    # =====================
    frame_color: int = 8                 # 外枠（灰）
    grid_color: int = 8                  # グリッド・寸法（灰）
    title_color: int = 8                 # タイトル（灰＝内容線の色と被らない中立色）
    point_color: int = 5                 # 平面図の点群（青）

    # 縦断図レイヤー色（同時表示される5本を全て別色に）
    crown_color: int = 1                 # 天端（赤）
    left_color: int = 3                  # 左壁（緑）
    right_color: int = 6                 # 右壁（紫）
    floor_color: int = 5                 # 地面（青）
    center_color: int = 8                # センター（灰・破線）

    # =====================
    # 出力ファイル名（出力先フォルダ）
    # =====================
    output_dir: str = "output"

    # ---------- 入出力ユーティリティ ----------
    @classmethod
    def load(cls, filepath: str = "config.json") -> "Config":
        """JSONから設定を読み込む。ファイルが無ければデフォルトを返す。"""
        path = Path(filepath)
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 未知のキーは無視し、既知のキーだけ適用（前方互換）
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)

    def save(self, filepath: str = "config.json") -> str:
        """現在の設定をJSONに書き出す。"""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
        return filepath

    @property
    def bg(self) -> tuple:
        """背景色をタプルで返す（内部処理用）。"""
        return tuple(self.background_color)


# モジュール読み込み時に config.json があれば自動で使う共有インスタンス
DEFAULT_CONFIG = Config.load(
    str(Path(__file__).resolve().parent / "config.json")
)


if __name__ == "__main__":
    # テンプレート config.json を生成する
    out = Path(__file__).resolve().parent / "config.json"
    Config().save(str(out))
    print(f"設定テンプレートを書き出しました: {out}")
