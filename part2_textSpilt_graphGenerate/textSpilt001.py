import os
import json
import time
from typing import List, Dict
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
import requests
import sys
from Models import os_setenv
from Models import get_gemini_2_flash
# 导入 stable diffusion 生图模块
from stable_diffusion import SD_Generate

from dotenv import load_dotenv

load_dotenv()
os_setenv()

class EnhancedCharacterGenerator:
    def __init__(self, novel_path: str):
        # 文件验证与读取
        if not os.path.exists(novel_path):
            raise FileNotFoundError(f"小说文件不存在: {novel_path}")
        
        # 获取小说名称
        self.novel_name = os.path.splitext(os.path.basename(novel_path))[0]
        
        # 创建小说专属目录
        self.novel_dir = os.path.join("part2_textSpilt_graphGenerate", self.novel_name)
        os.makedirs(self.novel_dir, exist_ok=True)
        
        # 创建子目录
        self.image_dir = os.path.join(self.novel_dir, "character_portraits")
        self.role_img_dir = os.path.join(self.novel_dir, "role_img")
        self.role_message_dir = os.path.join(self.novel_dir, "role_message")
        
        for directory in [self.image_dir, self.role_img_dir, self.role_message_dir]:
            os.makedirs(directory, exist_ok=True)
        
        with open(novel_path, 'r', encoding='utf-8') as file:
            self.novel_text = file.read(5000)  # 读取前5000字保证覆盖率
            
        if not self.novel_text:
            raise ValueError("小说文本为空")

        #设置文本模型
        self.extraction_llm = get_gemini_2_flash()
       
        # 初始化存储结构
        self.character_profiles = {}
        
        # 初始化 SD 生成器
        self.sd_generator = SD_Generate()

    def create_extraction_prompt(self) -> ChatPromptTemplate:
        """优化后的特征提取提示模板"""
        template = """
                    你是一个专业的小说分析与Stable Diffusion 提示词优化专家，擅长分析小说提取出所有角色并生成对角色的高质量的 Stable Diffusion 正向提示词（Positive Prompt）。

                    任务说明:
                    请将用户输入的小说中所有出现的人物（如果有姓名，则沿用，否则自己取一个代号）提取出来，并生成每个角色的专属画像提示词，以方便后序绘图。
                    注意：每个描述里面只能包含一个人物，并且生成的提示词是你根据小说推理出来的人物正面画像，而不是原文中的一段描述，必须是单人正面画像描述，去除多余的动作。

                    提示词要求:
                    1. 词组化:
                    - 将输入文本拆解为符合 Stable Diffusion 语法的关键词短语
                    - 每个词组之间使用英文逗号 , 分隔

                    2. 层次化:
                    按以下类别构造提示词:
                    - 人物特征 (如 blonde hair, long hair, smiling)
                    - 光线条件 (如 sunlight, bright)
                    - 画质增强 (如 ultra-detailed, 8K, masterpiece)
                
                    -视角构图（如 wide angle, from above, view of back, close-up, full body）

                    -画质增强（如 ultra-detailed, 8K, hires, masterpiece, best quality）

                    -优化画质：在提示词中添加高质量关键词，如 masterpiece, best quality, ultra-detailed，并结合 unreal engine rendered 等以提升细节。

                    -个性化调整：支持用户对特定关键词增加权重（如 (golden hair:1.2)），强调某些特征的视觉表现力,必须包含。

                    -避免负面元素：不包含 ugly, deformed, blurry, low quality 等负面词汇，以确保生成的图像美观、符合需求。

                    -调整词序：遵循 主语（人物）→ 光线 → 视角 → 画质 的顺序，确保提示词逻辑清晰，符合 AI 解析方式。
                    3. 只生成对人物正面外观的描述，禁止生成环境描写等其他项内容
                    生成严格符合以下格式的JSON响应:
                    {{
                        "characters": [{{
                            "name": "姓名",
                            "photo_prompt": "生成的高质量Stable Diffusion正向提示词(英文，8-10个词组左右)"
                        }}]
                    }}
                    """
        human_template = "文本节选：\n{excerpt}\n\n请按规范提取角色信息并创建二次元绘画风格提示词。"
        
        return ChatPromptTemplate.from_messages([
            ("system", template),
            ("human", human_template)
        ])

    def extract_character_data(self) -> List[Dict]:
        """带重试机制的特征提取"""
        retries = 3
        parser = JsonOutputParser()
        
        for attempt in range(retries):
            try:
                chain = self.create_extraction_prompt() | self.extraction_llm | parser
                response = chain.invoke({"excerpt": self.novel_text[:3000]})
                
                # 添加数据完整性检查
                if not isinstance(response, dict) or "characters" not in response:
                    raise ValueError("响应格式不正确")
                    
                if not isinstance(response["characters"], list):
                    raise ValueError("角色列表格式错误")
                
                print("成功提取角色列表：")
                for char in response["characters"]:
                    if not all(key in char for key in ["name", "photo_prompt"]):
                        raise ValueError(f"角色{char.get('name','未知')}字段缺失")
                    print(f"• {char['name']} ")
                    print(f"  摄影提示词: {char['photo_prompt']}")
                return response["characters"]
            
            except Exception as e:
                print(f"第{attempt+1}次尝试失败: {str(e)}")
                if attempt == retries - 1:
                    raise RuntimeError(f"特征提取失败：{str(e)}")
                time.sleep(1.5)

    def generate_character_image(self, character: Dict, char_index: str) -> dict:
        """使用 Stable Diffusion 生成角色图像"""
        try:
            # 准备提示词
            prompt = character["photo_prompt"]
            negative_prompt = "NSFW,logo,text,blurry,bad proportions,cropped,watermark,signature,low quality,out of focus,bad anatomy,username,sketches,lowres,normal quality,grayscale,monochrome,worstquality"
            
            # 生成图像
            self.sd_generator.generate_prompt(
                prompt=prompt,
                negative_prompt=negative_prompt,
                seed=-1,
                batch_size=2
            )
            self.sd_generator.get_img()
            
            # 构建完整的保存路径（不包含序号和扩展名）
            base_save_path = os.path.join("part2_textSpilt_graphGenerate", self.novel_name, "role_img", character["name"])
            seed_index = self.sd_generator.save_img(base_save_path)
            
            # 构建选中图片的完整路径
            selected_image_path = f"{base_save_path}_{seed_index}.png"
            
            return {
                "name": character["name"],
                "prompt": prompt,
                "seed": self.sd_generator.seeds[seed_index],
                "path": selected_image_path
            }
            
        except Exception as e:
            print(f"生成{character['name']}图像失败：{str(e)}")
            return None

    def process_novel(self) -> Dict:
        """主处理流程"""
        characters = self.extract_character_data()
        
        for i, char in enumerate(characters, 1):
            print(f"\n正在生成 [{char['name']}] 的角色形象...")
            char_data = self.generate_character_image(char, f"{i:03d}")
            
            if char_data:
                self.character_profiles[f"{i:03d}"] = char_data
        
        # 保存元数据
        json_path = os.path.join(self.novel_dir, "role_message", "character_archive.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.character_profiles, f, ensure_ascii=False, indent=2)
        
        print("\n处理完成！生成角色数：", len(self.character_profiles))
        return self.character_profiles

# 使用示例
if __name__ == "__main__":
    generator = EnhancedCharacterGenerator(
        novel_path="part2_textSpilt_graphGenerate\\files\\斗破苍穹节选.txt"
    )
    result = generator.process_novel()
    
    # 打印首个人物信息
    if result:
        print("\n示例人物数据：")
        print(json.dumps(result[next(iter(result))], ensure_ascii=False, indent=2))