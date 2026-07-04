import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=" * 60)
print("SYSTEM STABILITY VERIFICATION")
print("=" * 60)

# 1. Check imports
print("\n[1] Checking core imports...")
try:
    from app.main import app
    from app.agents.base_agent import BaseAgent
    from app.memory import MemoryInterface, ShortTermMemory, PersistentMemory, MemoryRetriever
    from app.agents.roles import get_role_for_task_type
    from app.services.log_service import LogService
    from app.core.providers.manager import ProviderManager
    print("✓ All core imports successful")
except ImportError as e:
    print(f"✗ Import failed: {e}")
    sys.exit(1)

# 2. Check provider
print("\n[2] Checking provider initialization...")
try:
    provider = ProviderManager.get_provider()
    assert provider is not None
    print(f"✓ Provider initialized: {type(provider).__name__}")
except Exception as e:
    print(f"✗ Provider init failed: {e}")
    sys.exit(1)

# 3. Check memory backends
print("\n[3] Checking memory backends...")
try:
    st = ShortTermMemory()
    st.add("test", "t1", "key1", "value1")
    assert st.get("test", "t1", "key1") == "value1"
    print("✓ ShortTermMemory working")
    
    pm = PersistentMemory()
    pm.add("test", "t2", "key2", {"x": 1})
    assert pm.get("test", "t2", "key2") == {"x": 1}
    print("✓ PersistentMemory working")
    
    mr = MemoryRetriever()
    mr.add("test", "t3", "k1", "v1")
    mr.add("test", "t3", "k2", "v2")
    assert mr.get("test", "t3", "k1") == "v1"
    ctx = mr.get_context_string("test", "t3")
    assert "v1" in ctx and "v2" in ctx
    print("✓ MemoryRetriever working")
except Exception as e:
    print(f"✗ Memory backends failed: {e}")
    sys.exit(1)

# 4. Check logging
print("\n[4] Checking logging service...")
try:
    ls = LogService()
    ls.info("test_source", "test message", {"key": "value"})
    logs = ls.list_logs("test_source")
    assert len(logs) > 0
    assert logs[0].level == "INFO"
    print("✓ LogService working")
except Exception as e:
    print(f"✗ LogService failed: {e}")
    sys.exit(1)

# 5. Check roles
print("\n[5] Checking roles system...")
try:
    role = get_role_for_task_type("seo_writing")
    assert role == "Etsy marketing copywriter"
    role_default = get_role_for_task_type("unknown_type")
    assert role_default == "copywriter"
    print("✓ Roles system working")
except Exception as e:
    print(f"✗ Roles system failed: {e}")
    sys.exit(1)

# 6. Check agents have run() method
print("\n[6] Checking agent run() methods...")
try:
    from app.core.agents.planner import PlannerAgent
    from app.core.agents.executor import ExecutorAgent
    from app.core.agents.generator import GeneratorAgent
    from app.core.agents.critic import CriticAgent
    from app.core.agents.fixer import FixerAgent
    from app.core.agents.qa import QAAgent
    
    agents = [
        PlannerAgent(), ExecutorAgent(), GeneratorAgent(),
        CriticAgent(), FixerAgent(), QAAgent()
    ]
    for agent in agents:
        assert hasattr(agent, 'run'), f"{agent.__class__.__name__} missing run()"
    print(f"✓ All {len(agents)} agents have run() method")
except Exception as e:
    print(f"✗ Agent run() check failed: {e}")
    sys.exit(1)

print("\n" + "=" * 60)
print("SYSTEM VERIFICATION PASSED ✓")
print("=" * 60)
print("\nCore system is stable and ready for advanced features.")