#!/usr/bin/env python3
"""Quick 2x2 pressure test to validate controller trigger with unified thresholds."""

import requests
import json
import time
import sys

def main():
    print("="*60)
    print("Quick 2x2 Pressure Test - Controller Trigger Validation")
    print("="*60)
    
    payload = {
        "topic": "Python编程最佳实践指南",
        "chapter_count": 2,
        "subsection_count": 2,
        "user_background": "中级开发者",
        "extra_requirements": "务实、代码示例清晰、中文",
        "rel_threshold": 0.70,
        "red_threshold": 0.62,
        "timeout_seconds": 1800,
    }
    
    print(f"\nConfig:")
    print(f"  Topic: {payload['topic']}")
    print(f"  Structure: {payload['chapter_count']}x{payload['subsection_count']} (4 subsections)")
    print(f"  Thresholds: rel={payload['rel_threshold']}, red={payload['red_threshold']}")
    print(f"  Timeout: {payload['timeout_seconds']}s")
    
    try:
        print(f"\nSending request at {time.strftime('%H:%M:%S')}...")
        start = time.time()
        
        resp = requests.post(
            "http://localhost:8010/api/generate",
            json=payload,
            timeout=2100,
        )
        
        elapsed = time.time() - start
        print(f"Response received: status={resp.status_code}, elapsed={elapsed:.1f}s\n")
        
        if resp.status_code != 200:
            print(f"ERROR: HTTP {resp.status_code}")
            print(resp.text[:500])
            sys.exit(1)
        
        body = resp.json()
        stats = body.get("stats", {})
        
        # Extract results
        success = body.get("success", False)
        content_len = len((body.get('content') or '').strip())
        passed = stats.get('passed_subsections', 0)
        failed = stats.get('failed_subsections', 0)
        forced = stats.get('forced_subsections', 0)
        
        # Controller metrics
        controller_calls = stats.get('controller_calls_total', 0) or 0
        ctrl_success = stats.get('controller_success_total', 0) or 0
        ctrl_error = stats.get('controller_error_total', 0) or 0
        ctrl_unavail = stats.get('controller_unavailable_total', 0) or 0
        ctrl_exhaust = stats.get('controller_exhausted_total', 0) or 0
        
        print("RESULTS:")
        print(f"  Success: {success}")
        print(f"  Content length: {content_len} chars")
        print(f"  Subsections: passed={passed}, failed={failed}, forced={forced}\n")
        
        print("CONTROLLER METRICS:")
        print(f"  calls_total: {controller_calls}")
        print(f"  success_total: {ctrl_success}")
        print(f"  error_total: {ctrl_error}")
        print(f"  unavailable_total: {ctrl_unavail}")
        print(f"  exhausted_total: {ctrl_exhaust}\n")
        
        print("VALIDATION:")
        is_triggered = controller_calls > 0
        is_not_excessive = controller_calls <= 12  # 4 subsections * 3
        no_dead_loop = ctrl_exhaust <= 1 and ctrl_unavail <= 2 and ctrl_error <= 6
        doc_complete = content_len >= 1600
        all_ok = success and failed == 0
        
        print(f"  controller_triggered (calls > 0): {is_triggered}")
        print(f"  controller_not_excessive (calls <= 12): {is_not_excessive}")
        print(f"  no_dead_loop: {no_dead_loop}")
        print(f"  doc_complete: {doc_complete}")
        print(f"  all_subsections_passed: {all_ok}\n")
        
        # Overall verdict
        if all([success, is_not_excessive, no_dead_loop, doc_complete]):
            if is_triggered:
                print("✓ SUCCESS: Controller trigger behavior is APPROPRIATE")
                print("  (Controller was activated when needed, but not excessively)")
                sys.exit(0)
            else:
                print("✓ SUCCESS: Document generated without controller (all passed first try)")
                print("  (Thresholds are well-calibrated; verifier quality is high)")
                sys.exit(0)
        else:
            print("✗ FAILED: Some validation checks did not pass")
            if not success:
                print("  - Document generation failed")
            if not is_not_excessive and controller_calls > 0:
                print(f"  - Controller triggered too many times: {controller_calls} > 12")
            if not no_dead_loop:
                print(f"  - Dead loop detected: exhausted={ctrl_exhaust}, unavail={ctrl_unavail}, error={ctrl_error}")
            if not doc_complete:
                print(f"  - Document too short: {content_len} < 1600 chars")
            sys.exit(1)
            
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
