"""
Figma API客户端
优化版 (支持 node-id 导出 + 结构缓存)
"""

import requests
from typing import Dict, List, Optional
from pathlib import Path
from config.config import Config


class FigmaClient:
    """Figma API客户端"""

    def __init__(self, access_token: str = None, file_key: str = None):

        self.access_token = access_token or Config.FIGMA_ACCESS_TOKEN
        self.file_key = file_key or Config.FIGMA_FILE_KEY

        if not self.access_token:
            raise ValueError("❌ 缺少 FIGMA_ACCESS_TOKEN")

        if not self.file_key:
            raise ValueError("❌ 缺少 FIGMA_FILE_KEY")

        self.base_url = "https://api.figma.com/v1"

        # 使用 Session 提升性能
        self.session = requests.Session()
        self.session.headers.update({
            "X-Figma-Token": self.access_token
        })

        # 文件结构缓存
        self._file_cache = None

    # =====================================================
    # 基础API
    # =====================================================

    def _get(self, url: str, params: dict = None) -> Dict:
        """统一 GET 请求"""

        response = self.session.get(url, params=params)

        try:
            response.raise_for_status()
        except Exception:
            raise RuntimeError(
                f"Figma API请求失败\n"
                f"URL: {url}\n"
                f"Response: {response.text}"
            )

        return response.json()

    # =====================================================
    # 文件结构
    # =====================================================

    def get_file_structure(self, force_refresh: bool = False) -> Dict:
        """获取Figma文件结构（带缓存）"""

        if self._file_cache and not force_refresh:
            return self._file_cache

        url = f"{self.base_url}/files/{self.file_key}"

        data = self._get(url)

        self._file_cache = data

        return data

    # =====================================================
    # 页面与Frame
    # =====================================================

    def list_all_pages(self) -> List[Dict]:
        """列出所有页面"""

        data = self.get_file_structure()

        pages = []

        for page in data["document"]["children"]:
            pages.append({
                "id": page["id"],
                "name": page["name"],
                "type": page.get("type")
            })

        return pages

    def list_frames_in_page(self, page_name: str) -> List[Dict]:
        """列出某页面所有Frame"""

        data = self.get_file_structure()

        frames = []

        for page in data["document"]["children"]:

            if page["name"] != page_name:
                continue

            for child in page.get("children", []):

                if child["type"] in ("FRAME", "COMPONENT"):

                    frames.append({
                        "id": child["id"],
                        "name": child["name"],
                        "type": child["type"]
                    })

        return frames

    def list_all_pages_and_frames(self):
        """打印所有页面结构（调试用）"""

        print("\n" + "=" * 60)
        print("📐 Figma文件结构")
        print("=" * 60)

        for page in self.list_all_pages():

            print(f"\n📄 页面: {page['name']} (ID: {page['id']})")

            frames = self.list_frames_in_page(page["name"])

            if not frames:
                print("   └─ (无Frame)")
                continue

            for frame in frames:
                print(f"   └─ 🖼️ {frame['name']} (ID: {frame['id']})")

        print("\n" + "=" * 60)

    # =====================================================
    # 查找Frame
    # =====================================================

    def find_frame_by_name(self, page_name: str, frame_name: str) -> Optional[str]:
        """通过名称查找Frame ID"""

        frames = self.list_frames_in_page(page_name)

        for frame in frames:
            if frame["name"] == frame_name:
                return frame["id"]

        return None

    # =====================================================
    # 导出图片
    # =====================================================

    def export_node_image(
        self,
        node_id: str,
        scale: float = 2,
        format: str = "png"
    ) -> bytes:
        """通过 node-id 导出图片"""

        url = f"{self.base_url}/images/{self.file_key}"

        params = {
            "ids": node_id,
            "scale": scale,
            "format": format
        }

        data = self._get(url, params)

        image_url = data["images"].get(node_id)

        if not image_url:
            raise RuntimeError(f"无法导出节点: {node_id}")

        img = self.session.get(image_url)

        img.raise_for_status()

        return img.content

    # =====================================================
    # 保存图片
    # =====================================================

    def save_node_to_file(
        self,
        node_id: str,
        output_path: Path,
        scale: float = 2,
        format: str = "png"
    ) -> Path:
        """通过 node-id 保存图片"""

        img_data = self.export_node_image(
            node_id=node_id,
            scale=scale,
            format=format
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "wb") as f:
            f.write(img_data)

        return output_path

    def save_frame_to_file(
        self,
        page_name: str,
        frame_name: str,
        output_path: Path,
        scale: float = 2
    ) -> Path:
        """通过名称保存Frame"""

        node_id = self.find_frame_by_name(page_name, frame_name)

        if not node_id:
            raise ValueError(
                f"未找到Frame: {page_name}/{frame_name}\n"
                f"请运行 list_all_pages_and_frames() 查看结构"
            )

        return self.save_node_to_file(
            node_id=node_id,
            output_path=output_path,
            scale=scale
        )