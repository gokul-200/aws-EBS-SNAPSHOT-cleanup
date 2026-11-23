# AWS EBS Snapshot Cleanup (Python Lambda + EventBridge)

A serverless automation project that checks EBS snapshots owned by your AWS account and removes snapshots that meet the configured cleanup criteria. Amazon EventBridge runs the Lambda function on a daily schedule, while Amazon CloudWatch Logs provides execution visibility.

> **Important:** Snapshot deletion is permanent. Validate every candidate against your backup-retention policy, recovery requirements, registered AMIs, and AWS Backup before enabling unattended deletion.

## At a glance

- **Runtime:** Python on AWS Lambda
- **AWS SDK:** boto3
- **Compute/storage APIs:** Amazon EC2 (EBS snapshots and volumes)
- **Schedule:** Amazon EventBridge (daily)
- **Logs:** Amazon CloudWatch Logs
- **Permissions:** IAM execution role

## Architecture

![EBS Snapshot Cleanup architecture](architecture.png)

### Components

| Component | Responsibility |
|---|---|
| Amazon EventBridge | Invokes the Lambda function on the configured daily schedule. |
| AWS Lambda | Runs the Python cleanup logic without managing servers. |
| boto3 / EC2 API | Lists account-owned snapshots and checks each snapshot's source volume. |
| IAM execution role | Grants the Lambda only the AWS API permissions it needs. |
| CloudWatch Logs | Stores the function's progress, errors, and final execution summary. |

## Animated execution flow

![Animated execution flow](execution-flow.gif)

### What the function does

1. EventBridge triggers the Lambda according to the daily schedule.
2. Lambda creates a boto3 EC2 client.
3. A paginator retrieves snapshots owned by the account (`OwnerIds=['self']`).
4. For each snapshot, the function reads its `VolumeId`.
5. It calls `DescribeVolumes` to check whether the original source volume still exists.
6. It evaluates the source volume:
   - **Source volume not found:** the snapshot is treated as a deletion candidate.
   - **Source volume exists but has no attachments:** the snapshot is treated as a deletion candidate.
   - **Source volume has one or more attachments:** the snapshot is skipped.
7. If `DRY_RUN = True`, the function logs what it *would* delete.
8. If `DRY_RUN = False`, it calls `DeleteSnapshot`.
9. The function prints a summary containing candidate, deleted, and skipped counts.

### Decision logic

```text
                    Snapshot found
                          |
                    Read VolumeId
                          |
                 Describe source volume
                          |
              +-----------+------------+
              |                        |
        Volume not found          Volume exists
              |                        |
         Candidate                Check attachments
                                       |
                            +----------+----------+
                            |                     |
                      No attachments        Has attachment(s)
                            |                     |
                        Candidate                Skip
                            |
                    DRY_RUN enabled?
                      /         \
                    Yes          No
                    |             |
                Log only     DeleteSnapshot
```

## Repository structure

```text
aws-ebs-snapshot-cleanup/
├── README.md
├── architecture.png
└── execution-flow.gif
```

If you keep the Lambda source in this repository, a practical layout is:

```text
aws-ebs-snapshot-cleanup/
├── lambda_function.py
├── README.md
├── architecture.png
└── execution-flow.gif
```

## Configuration

The cleanup behaviour is controlled in the Lambda source:

```python
DRY_RUN = True
```

- `True`: report deletion candidates without deleting anything.
- `False`: call `ec2.delete_snapshot()` for each candidate.

**Recommended rollout:** deploy with `DRY_RUN = True`, inspect the CloudWatch logs and verify the candidate list, then change to `False` only after confirming the cleanup criteria are appropriate for your environment.

## IAM permissions

Attach permissions to the **Lambda execution role**, not to your personal IAM user. The role name is visible in **Lambda → Configuration → Permissions**.

The following is an example policy for the behaviour described above. Replace the account ID and region if your deployment differs.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadEc2SnapshotAndVolumeState",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeSnapshots",
        "ec2:DescribeVolumes"
      ],
      "Resource": "*"
    },
    {
      "Sid": "DeleteOwnedSnapshotsInThisRegion",
      "Effect": "Allow",
      "Action": "ec2:DeleteSnapshot",
      "Resource": "arn:aws:ec2:us-east-1:691529317365:snapshot/*"
    },
    {
      "Sid": "WriteLambdaLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "*"
    }
  ]
}
```

The `DeleteSnapshot` resource above is scoped to account `691529317365` in `us-east-1`, matching the execution error observed during testing. Keep the region and account scope aligned with the Lambda's deployment. If the role already has the standard `AWSLambdaBasicExecutionRole`, the CloudWatch Logs permissions may already be covered.

An explicit deny in an AWS Organizations service control policy, permissions boundary, or another applicable policy can still block deletion even when this identity policy allows it.

## Create the daily schedule with EventBridge

AWS now presents scheduled event rules under Amazon EventBridge (formerly CloudWatch Events).

1. Open **Amazon EventBridge** in the same AWS Region as the Lambda function.
2. Go to **Rules** and choose **Create rule** (or use **Scheduler → Create schedule**).
3. Give it a descriptive name, for example `daily-ebs-snapshot-cleanup`.
4. Choose a recurring schedule:
   - For a rule using a cron expression, `cron(0 2 * * ? *)` means every day at 02:00 **UTC**.
   - If using EventBridge Scheduler, select the desired timezone where the console offers timezone configuration.
5. Set the target to your EBS cleanup Lambda function.
6. Allow EventBridge to invoke the Lambda when prompted.
7. Create the schedule and confirm it is enabled.

**Time-zone note:** EventBridge rule cron expressions are evaluated in UTC. India Standard Time is UTC+05:30, so 02:00 UTC is 07:30 IST. Choose a time that fits your backup and maintenance windows.

## Testing and verification

### 1. Test in dry-run mode

Set `DRY_RUN = True`, deploy the code, and invoke a Lambda test event. Inspect the execution logs.

Example dry-run log:

```text
Checking snapshot: snap-xxxxxxxx, Volume: vol-xxxxxxxx
[DRY RUN] Would delete snapshot snap-xxxxxxxx: Source volume no longer exists.
========== CLEANUP SUMMARY ==========
Deletion candidates: 1
Deleted snapshots: 0
Skipped snapshots: 0
Dry run enabled: True
```

### 2. Enable deletion only after review

After validating the candidate snapshots and retention requirements, set `DRY_RUN = False`, deploy, and run a controlled test.

A successful deletion should produce a log similar to:

```text
Deleted snapshot snap-xxxxxxxx
========== CLEANUP SUMMARY ==========
Deletion candidates: 1
Deleted snapshots: 1
Skipped snapshots: 0
Dry run enabled: False
```

### 3. Confirm in the EC2 console

Open **EC2 → Elastic Block Store → Snapshots** in the same Region. Search for the snapshot ID and confirm its state. Snapshot deletion may take time to be reflected in the console.

## Safety and operational considerations

- **Unattached does not always mean unused.** A volume may be deliberately detached for maintenance, migration, or recovery. This project's current rule treats a snapshot of any unattached source volume as a candidate.
- **Stopped instances:** an EBS volume attached to a stopped instance still has an attachment and is therefore kept by this logic.
- **AMI dependencies:** check whether a snapshot backs a registered AMI. Do not delete snapshots required by images or deployment workflows.
- **AWS Backup:** manage AWS Backup recovery points through AWS Backup lifecycle and retention controls rather than treating them as ordinary manually managed snapshots.
- **Retention policy:** consider tagging snapshots with retention dates or excluding protected snapshots before using this in production.
- **Region scope:** EBS snapshots are regional. Deploy and schedule the Lambda in each Region you intend to clean up.
- **Deletion is irreversible:** there is no undo for a deleted snapshot. Maintain independent backups for important data.

### Recommended production improvement

Before expanding the automation, consider adding an explicit opt-in tag requirement, such as:

```text
CleanupEligible = true
```

Then only delete snapshots that both meet the volume criteria and carry the approved tag. This reduces the risk of deleting backups that are intentionally retained.

## Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| `UnauthorizedOperation` on `DeleteSnapshot` | Lambda role lacks deletion permission | Add `ec2:DeleteSnapshot` to the Lambda execution role, scoped to the intended account and Region. |
| `UnauthorizedOperation` on `DescribeVolumes` | Lambda role lacks read permission | Add `ec2:DescribeVolumes` with `Resource: "*"` as required by EC2 describe APIs. |
| `candidates: 0` | No snapshots meet the current criteria | Review source volume state and the function's filtering logic. |
| `deleted: 0`, `dry_run: true` | Dry-run mode is enabled | This is expected; no snapshots are deleted. |
| Lambda test succeeds but scheduled run does not happen | Schedule disabled, wrong Region, or target permission issue | Check EventBridge schedule state, target, invocation permission, and Lambda metrics. |
| Snapshot is still visible after deletion call | Deletion may still be processing, or the call failed | Check the Lambda logs and refresh the Snapshots page in the correct Region. |

## Monitoring

Use **CloudWatch → Log groups → `/aws/lambda/<your-function-name>`** to inspect:
- Snapshot IDs checked
- Candidate and skip decisions
- Deletion success or failure messages
- Final candidate, deleted, and skipped counts

For production use, consider CloudWatch alarms for Lambda errors and missed/failed scheduled invocations.

## Project status

- [x] Lambda identifies snapshots whose source volume is missing.
- [x] Lambda identifies snapshots whose source volume has no attachments.
- [x] Lambda keeps snapshots whose source volume has an attachment.
- [x] Dry-run mode is available for safe validation.
- [x] IAM deletion permission configured for the target account and Region.
- [ ] Daily EventBridge schedule configured and verified.
- [ ] Production retention and snapshot-tag safeguards reviewed.

## Disclaimer

This project is an infrastructure-cleanup example, not a replacement for a formal backup lifecycle policy. Test in a non-production account first and review every deletion condition before enabling unattended execution.
