"""
图像对比模块
提供图像相似度计算和差异可视化功能
"""

from PIL import Image
import cv2
import numpy as np
from pathlib import Path
from typing import Tuple, Optional


class ImageCompare:
    """图像对比工具"""

    def __init__(self, threshold: float = 95.0):
        """
        初始化

        Args:
            threshold: 相似度阈值(0-100)
        """
        self.threshold = threshold

    # ------------------------------------------------
    # 工具方法
    # ------------------------------------------------
    def _check_image_exists(self, img_path: Path):
        """检查图片是否存在"""
        if not Path(img_path).exists():
            raise FileNotFoundError(f"图片不存在: {img_path}")

    # ------------------------------------------------
    # 尺寸统一
    # ------------------------------------------------
    def resize_to_match(self,
                        img1_path: Path,
                        img2_path: Path) -> Tuple[Image.Image, Image.Image]:
        """
        调整两张图片到相同尺寸
        """

        self._check_image_exists(img1_path)
        self._check_image_exists(img2_path)

        img1 = Image.open(img1_path)
        img2 = Image.open(img2_path)

        width = min(img1.width, img2.width)
        height = min(img1.height, img2.height)

        img1_resized = img1.resize((width, height), Image.Resampling.LANCZOS)
        img2_resized = img2.resize((width, height), Image.Resampling.LANCZOS)

        return img1_resized, img2_resized

    # ------------------------------------------------
    # 相似度计算
    # ------------------------------------------------
    def calculate_similarity(self,
                             img1_path: Path,
                             img2_path: Path) -> float:
        """
        计算两张图片相似度(0-100)
        """

        img1_pil, img2_pil = self.resize_to_match(img1_path, img2_path)

        img1_array = np.array(img1_pil)
        img2_array = np.array(img2_pil)

        diff = cv2.absdiff(img1_array, img2_array)

        diff_sum = np.sum(diff)
        max_diff = diff.size * 255

        similarity = 100 - (diff_sum / max_diff * 100)

        return float(round(float(similarity), 2))

    # ------------------------------------------------
    # MSE
    # ------------------------------------------------
    def calculate_mse(self,
                      img1_path: Path,
                      img2_path: Path) -> float:
        """
        均方误差
        """

        img1_pil, img2_pil = self.resize_to_match(img1_path, img2_path)

        img1_array = np.array(img1_pil)
        img2_array = np.array(img2_pil)

        mse = np.mean((img1_array.astype("float") - img2_array.astype("float")) ** 2)

        return float(round(float(mse), 4))

    # ------------------------------------------------
    # SSIM
    # ------------------------------------------------
    def calculate_ssim(self,
                       img1_path: Path,
                       img2_path: Path) -> float:
        """
        结构相似度
        """

        from skimage.metrics import structural_similarity as ssim

        img1_pil, img2_pil = self.resize_to_match(img1_path, img2_path)

        img1_gray = np.array(img1_pil.convert('L'))
        img2_gray = np.array(img2_pil.convert('L'))

        score = ssim(img1_gray, img2_gray)

        return round(score, 4)

    # ------------------------------------------------
    # 生成差异图
    # ------------------------------------------------
    def generate_diff_image(self,
                            img1_path: Path,
                            img2_path: Path,
                            output_path: Path,
                            highlight_color=(0, 0, 255)) -> Path:
        """
        生成差异高亮图
        """

        img1 = cv2.imread(str(img1_path))
        img2 = cv2.imread(str(img2_path))

        if img1 is None or img2 is None:
            raise ValueError("图片读取失败")

        height = min(img1.shape[0], img2.shape[0])
        width = min(img1.shape[1], img2.shape[1])

        img1 = cv2.resize(img1, (width, height))
        img2 = cv2.resize(img2, (width, height))

        diff = cv2.absdiff(img1, img2)

        gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 25, 255, cv2.THRESH_BINARY)

        highlight = img2.copy()
        highlight[mask > 0] = highlight_color

        output_path.parent.mkdir(parents=True, exist_ok=True)

        cv2.imwrite(str(output_path), highlight)

        return output_path

    # ------------------------------------------------
    # 并排对比
    # ------------------------------------------------
    def generate_side_by_side(self,
                              img1_path: Path,
                              img2_path: Path,
                              output_path: Path,
                              labels=("Figma设计", "网站实际")) -> Path:
        """
        生成并排对比图
        """

        img1 = cv2.imread(str(img1_path))
        img2 = cv2.imread(str(img2_path))

        height = min(img1.shape[0], img2.shape[0])

        aspect1 = img1.shape[1] / img1.shape[0]
        aspect2 = img2.shape[1] / img2.shape[0]

        width1 = int(height * aspect1)
        width2 = int(height * aspect2)

        img1 = cv2.resize(img1, (width1, height))
        img2 = cv2.resize(img2, (width2, height))

        font = cv2.FONT_HERSHEY_SIMPLEX

        cv2.putText(img1, labels[0], (10, 40), font, 1, (255, 255, 255), 2)
        cv2.putText(img2, labels[1], (10, 40), font, 1, (255, 255, 255), 2)

        combined = np.hstack((img1, img2))

        output_path.parent.mkdir(parents=True, exist_ok=True)

        cv2.imwrite(str(output_path), combined)

        return output_path

    # ------------------------------------------------
    # 判断是否相似
    # ------------------------------------------------
    def is_similar(self,
                   img1_path: Path,
                   img2_path: Path,
                   threshold: Optional[float] = None) -> bool:

        threshold = threshold or self.threshold

        similarity = self.calculate_similarity(img1_path, img2_path)

        return similarity >= threshold

    # ------------------------------------------------
    # 完整报告
    # ------------------------------------------------
    def get_comparison_report(self,
                              img1_path: Path,
                              img2_path: Path) -> dict:

        similarity = self.calculate_similarity(img1_path, img2_path)
        mse = self.calculate_mse(img1_path, img2_path)

        img1 = Image.open(img1_path)
        img2 = Image.open(img2_path)

        report = {
            "similarity": float(similarity),
            "mse": float(mse),
            "threshold": float(self.threshold),
            "passed": bool(similarity >= self.threshold),
            "image1": {
                "path": str(img1_path),
                "size": img1.size
            },
            "image2": {
                "path": str(img2_path),
                "size": img2.size
            }
        }

        return report


# ==============================
# 本地测试
# ==============================

if __name__ == "__main__":

    print("\n🚀 图像对比测试")
    print("=" * 50)

    comparator = ImageCompare(threshold=95)

    img1 = Path("screenshots/figma/test.png")
    img2 = Path("screenshots/web/test.png")

    if img1.exists() and img2.exists():

        similarity = comparator.calculate_similarity(img1, img2)

        print("相似度:", similarity)

        diff = comparator.generate_diff_image(
            img1,
            img2,
            Path("reports/diff.png")
        )

        print("差异图:", diff)

    else:
        print("请准备测试图片")