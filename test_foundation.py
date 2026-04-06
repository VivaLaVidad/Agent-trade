import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database.models import Base, ClientLead
from dotenv import load_dotenv

load_dotenv()

# 1. 设置测试数据库引擎
DB_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./test_stealth.db")
engine = create_async_engine(DB_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def run_encryption_test():
    print("🚀 开始暗箱加密测试...\n")
    
    # 初始化表结构
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    
    # --- 测试 1：以正常业务逻辑写入数据 ---
    async with AsyncSessionLocal() as session:
        new_lead = ClientLead(
            client_name="Elon Musk",
            client_email="elon@spacex.com",  # 敏感信息
            contact_info="+1-800-123-4567",  # 敏感信息
            company="SpaceX",
            source="LinkedIn",
            status="new",
            priority="high",
            is_active=True
        )
        session.add(new_lead)
        await session.commit()
        print("✅ [业务层] 数据已成功写入数据库！")

    # --- 测试 2：黑客/内鬼视角 (跳过 ORM，直接查底层硬盘数据) ---
    async with engine.connect() as conn:
        # 直接执行原生 SQL 查询
        result = await conn.execute(text("SELECT client_name, client_email FROM client_leads"))
        row = result.fetchone()
        print("\n🕵️‍♂️ [底层硬盘视角 / 数据库管理员视角] :")
        print(f"  -> 客户姓名 (明文列): {row[0]}")
        print(f"  -> 客户邮箱 (加密列): {row[1]}") 
        # 如果这里打印出来是一串看不懂的 bytes (如 b'\x12\x34\x56...')，说明暗箱加密成功！

    # --- 测试 3：应用层读取 (验证解密是否无缝) ---
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        result = await session.execute(select(ClientLead).where(ClientLead.client_name == "Elon Musk"))
        lead = result.scalar_one()
        print("\n💼 [业务层读取视角] :")
        print(f"  -> 客户姓名: {lead.client_name}")
        print(f"  -> 客户邮箱 (自动解密): {lead.client_email}")
        
    print("\n🎉 地基测试完成！")

if __name__ == "__main__":
    asyncio.run(run_encryption_test())
