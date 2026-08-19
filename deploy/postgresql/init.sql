-- PostgreSQL 初始化脚本
-- 挂载路径：deploy/postgresql/init.sql → /docker-entrypoint-initdb.d/init.sql
-- 仅在数据库首次初始化时执行（数据目录为空时）

-- 创建用户表（实际由 Alembic 管理，此处仅为初始化占位）
-- Alembic 迁移将创建完整表结构