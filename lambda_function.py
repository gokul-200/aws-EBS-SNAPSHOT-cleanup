import boto3
from botocore.exceptions import ClientError

ec2 = boto3.client('ec2')

# SAFETY: First run with True to check what would be deleted.
# Change to False after verifying the CloudWatch logs.
DRY_RUN = False


def lambda_handler(event, context):

    paginator = ec2.get_paginator('describe_snapshots')

    deleted_count = 0
    candidate_count = 0
    skipped_count = 0

    # Get all snapshots owned by this AWS account
    for page in paginator.paginate(OwnerIds=['self']):

        for snapshot in page['Snapshots']:

            snapshot_id = snapshot['SnapshotId']
            volume_id = snapshot.get('VolumeId')

            print(f"Checking snapshot: {snapshot_id}, Volume: {volume_id}")

            if not volume_id:
                print(f"Skipping {snapshot_id}: No source volume ID.")
                skipped_count += 1
                continue

            try:
                # Check whether the source volume exists
                volume_response = ec2.describe_volumes(
                    VolumeIds=[volume_id]
                )

                volume = volume_response['Volumes'][0]

                # Check whether volume has any EC2 attachment
                attachments = volume.get('Attachments', [])

                if len(attachments) == 0:

                    candidate_count += 1

                    if DRY_RUN:
                        print(
                            f"[DRY RUN] Would delete snapshot "
                            f"{snapshot_id} from unattached volume {volume_id}"
                        )
                    else:
                        ec2.delete_snapshot(
                            SnapshotId=snapshot_id
                        )

                        deleted_count += 1

                        print(
                            f"Deleted snapshot {snapshot_id}"
                        )

                else:
                    print(
                        f"Keeping snapshot {snapshot_id}: "
                        f"Volume {volume_id} is attached to an EC2 instance."
                    )

                    skipped_count += 1

            except ClientError as e:

                error_code = e.response['Error']['Code']

                if error_code == 'InvalidVolume.NotFound':

                    candidate_count += 1

                    if DRY_RUN:
                        print(
                            f"[DRY RUN] Would delete snapshot "
                            f"{snapshot_id}: Source volume no longer exists."
                        )
                    else:
                        try:
                            ec2.delete_snapshot(
                                SnapshotId=snapshot_id
                            )

                            deleted_count += 1

                            print(
                                f"Deleted snapshot {snapshot_id}: "
                                f"Source volume no longer exists."
                            )

                        except ClientError as delete_error:
                            print(
                                f"Deletion failed for {snapshot_id}: "
                                f"{delete_error}"
                            )

                else:
                    print(
                        f"Error checking volume {volume_id}: {e}"
                    )

                    skipped_count += 1

    print("========== CLEANUP SUMMARY ==========")
    print(f"Deletion candidates: {candidate_count}")
    print(f"Deleted snapshots: {deleted_count}")
    print(f"Skipped snapshots: {skipped_count}")
    print(f"Dry run enabled: {DRY_RUN}")

    return {
        'statusCode': 200,
        'body': {
            'candidates': candidate_count,
            'deleted': deleted_count,
            'skipped': skipped_count,
            'dry_run': DRY_RUN
        }
    }
