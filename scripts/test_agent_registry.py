import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.registry import get_agent, list_agents, AGENT_REGISTRY
print("=" * 60)
print("AGENT REGISTRY TEST")
print("=" * 60)

# 1. List all registered agents
print("\n[1] Listing all registered agents...")
agents = list_agents()
print(f"✓ Found {len(agents)} registered agent(s):")
for agent_name in agents:
    print(f"  - {agent_name}")

# 2. Instantiate each agent
print("\n[2] Instantiating each agent...")
for agent_name in agents:
    try:
        agent = get_agent(agent_name)
        assert hasattr(agent, 'run'), f"{agent_name} missing run() method"
        print(f"✓ {agent_name}: {agent.__class__.__name__}")
    except Exception as e:
        print(f"✗ {agent_name}: {e}")
        sys.exit(1)

# 3. Test error handling for unknown agent
print("\n[3] Testing error handling for unknown agent...")
try:
    get_agent("nonexistent_agent")
    print("✗ Should have raised ValueError for unknown agent")
    sys.exit(1)
except ValueError as e:
    assert "Unknown agent" in str(e)
    print(f"✓ Correctly rejected unknown agent")
    print(f"  Error: {str(e)[:80]}...")

print("\n" + "=" * 60)
print("AGENT REGISTRY TEST PASSED ✓")
print("=" * 60)
print("\nAgent expansion layer is ready for new agents.")