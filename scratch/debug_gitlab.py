import gitlab
import sys

def main():
    token = input("Enter GitLab Personal Access Token: ").strip()
    if not token:
        print("Token cannot be empty.")
        sys.exit(1)
        
    try:
        print("Connecting to gitlab.com ...")
        gl = gitlab.Gitlab("https://gitlab.com", private_token=token)
        gl.auth()
        print(f"✅ Authenticated as: {gl.user.username} (ID: {gl.user.id}, email: {gl.user.email})")
        
        project_path = "cloudit-africa/boots-on-the-ground"
        print(f"\nFetching project '{project_path}' ...")
        project = gl.projects.get(project_path)
        print(f"✅ Found project: {project.name_with_namespace} (ID: {project.id})")
        print(f"   Visibility: {project.visibility}")
        print(f"   Empty Repository flag: {getattr(project, 'empty_repo', 'Unknown')}")
        print(f"   Default branch: {getattr(project, 'default_branch', 'None')}")
        
        # 1. Test listing branches
        print("\nListing branches via API ...")
        try:
            branches = project.branches.list(all=True)
            print(f"✅ Success! Found {len(branches)} branch(es):")
            for b in branches:
                print(f"   - {b.name} (Protected: {b.protected})")
        except Exception as e:
            print(f"❌ Failed to list branches: {e}")
            
        # 2. Test listing merge requests
        print("\nListing merge requests via API ...")
        try:
            mrs = project.mergerequests.list(state="all", all=True)
            print(f"✅ Success! Found {len(mrs)} merge request(s):")
            for mr in mrs[:10]:
                print(f"   - !{mr.iid}: {mr.title} ({mr.state})")
            if len(mrs) > 10:
                print(f"   ... and {len(mrs) - 10} more")
        except Exception as e:
            print(f"❌ Failed to list merge requests: {e}")

    except Exception as e:
        print(f"❌ Authentication/Connection Error: {e}")

if __name__ == "__main__":
    main()
