# ACMS End-to-End Test Guide for Claude

## Overview
This guide provides Claude with instructions to perform comprehensive end-to-end testing of all ACMS tool functions. Use this when you need to validate ACMS functionality or verify system status.

## Test Execution Instructions

### Prerequisites
- Ensure you're connected to acms via `/mcp` command
- acms should be running (container-apiserver version 0.4.1 expected)
- Use TodoWrite tool to track progress through all test categories

### Test Categories (Execute in Order)

#### 1. System Status and Basic Info
```
mcp__acms__system_status
mcp__acms__container_list (with all=true)
mcp__acms__image_list
```
**Expected**: System running, version info, current containers/images listed

#### 2. Image Operations
```
mcp__acms__image_pull (test with: redis or nginx or postgres)
mcp__acms__image_inspect (inspect the pulled image)
mcp__acms__image_tag (tag the image with a test name)
mcp__acms__image_delete (delete the tagged image using array parameter)
mcp__acms__image_prune
```
**Critical Test**: Verify `image_delete` works with array parameter like `["image:tag"]`

#### 3. Container Lifecycle
```
mcp__acms__container_create (create from pulled image)
mcp__acms__container_run (run detached container)
mcp__acms__container_start (start the created container)
mcp__acms__container_list (verify states)
mcp__acms__container_stop (stop specific containers using array parameter)
mcp__acms__container_kill (kill specific containers using array parameter)
mcp__acms__container_delete (delete specific containers using array parameter)
```
**Critical Test**: Verify individual container targeting works with arrays

#### 4. Container Interaction
```
mcp__acms__container_run (create a long-running container like nginx)
mcp__acms__container_logs (get container logs)
mcp__acms__container_inspect (get detailed container info)
mcp__acms__container_exec (execute command like "nginx -v" or "ls /etc")
```
**Critical Test**: Container exec should work with service containers (nginx, not alpine)

#### 5. Network Operations
```
mcp__acms__network_list
mcp__acms__network_create (create test network)
mcp__acms__network_inspect (inspect created network)
mcp__acms__network_delete (delete specific network using array parameter)
```
**Critical Test**: Verify `network_delete` works with array parameter like `["network-name"]`

#### 6. Volume Operations
```
mcp__acms__volume_list
mcp__acms__volume_create (create test volume with size)
mcp__acms__volume_inspect (inspect created volume)
mcp__acms__volume_delete (delete by name using array parameter)
```
**Expected**: Volume operations should always work (historically reliable)

#### 7. Builder Operations
```
mcp__acms__builder_status
mcp__acms__builder_start
mcp__acms__builder_status (confirm running)
mcp__acms__builder_stop
mcp__acms__builder_delete
```

#### 8. Registry Operations
```
mcp__acms__registry_default
```
**Note**: Limited testing scope (no credentials for login/logout)

#### 9. System Operations
```
mcp__acms__system_logs (with last="30s" or similar)
mcp__acms__system_dns_list
mcp__acms__system_dns_default
```

### Key Testing Principles

#### Array Parameter Validation (CRITICAL)
The most important test is verifying these functions work with array parameters:
- `image_delete(images=["image1:tag", "image2:tag"])`
- `container_stop(containers=["container1", "container2"])`
- `container_kill(containers=["container1"])`
- `container_delete(containers=["container1", "container2"])`
- `network_delete(networks=["network1"])`

#### Container Exec Requirements
- Use **service containers** (nginx, apache) not utility containers (alpine, busybox)
- Service containers maintain running state and have proper PATH configuration
- Test commands: `nginx -v`, `nginx -t`, `ls /etc/nginx`

#### Expected Success Patterns
- **100% Success Rate**: All functions should work (as of Session 5)
- **Individual Targeting**: Array parameters should work for all delete/stop/kill operations
- **Bulk Operations**: `--all` flags should work as fallback
- **Container Persistence**: Containers may persist across test sessions

### Error Patterns to Watch For

#### Previous Issues (Should be RESOLVED)
```
Error: Input validation error: '["item-name"]' is not valid under any of the given schemas
```
**If you see this**: The array parameter validation issues have returned

#### Expected Errors (Normal Behavior)
- Cannot delete running containers (must stop first)
- Cannot delete system/default networks
- Container exec fails on stopped containers
- DNS domain creation may fail due to permissions

### Test Execution Template

```markdown
# ACMS Comprehensive End-to-End Test Results

## Test Summary
**Date**: [DATE]
**Success Rate**: X/40 functions (Y%)
**Status**: [BREAKTHROUGH/REGRESSION/STABLE]

## System Environment
- **ACMS Version**: [from system_status]
- **Images Available**: [count from image_list]
- **Running Containers**: [count from container_list]

## Results by Category
### ✅ System Status: [X/3 functions]
### ✅ Image Operations: [X/5 functions]
### ✅ Container Lifecycle: [X/5 functions]
### ✅ Container Interaction: [X/4 functions]
### ✅ Network Operations: [X/4 functions]
### ✅ Volume Operations: [X/4 functions]
### ✅ Builder Operations: [X/4 functions]
### ✅ Registry Operations: [X/1 functions]
### ✅ System Operations: [X/3 functions]

## Key Findings
- Array parameter validation: [WORKING/BROKEN]
- Container exec functionality: [WORKING/BROKEN]
- Individual item targeting: [WORKING/BROKEN]

## Issues Discovered
[List any failures or regressions]

## Recommendations
[Next steps or concerns]
```

### Save Results
Always save comprehensive test results to:
`/$HOME/code/github/acms/acms_comprehensive_test_results_[D].md`

Where D is the date. Include:
- Complete test results by category
- Success/failure analysis
- Comparison to previous sessions
- Technical evidence of breakthroughs or regressions
- Context for future development sessions

## Usage Instructions for Claude

1. **Read this guide completely** before starting tests
2. **Use TodoWrite tool** to track progress through all 9 categories
3. **Test array parameters thoroughly** - this is the most critical functionality
4. **Document everything** - save detailed results for future sessions
5. **Compare to previous sessions** - note improvements or regressions
6. **Focus on breakthroughs** - identify what changes between sessions

## Expected Outcome
ACMS should achieve 100% functionality with all array parameter validation issues resolved. Any regression from this state is significant and should be thoroughly documented.
