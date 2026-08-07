import os
import cv2
import numpy as np


def read_image_unicode(path, flags=cv2.IMREAD_COLOR):
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, flags)
    except Exception:
        return None


def red_mask_rgb(color_np):
    hsv = cv2.cvtColor(color_np, cv2.COLOR_RGB2HSV)
    lower1 = np.array([0, 50, 50], dtype=np.uint8)
    upper1 = np.array([10, 255, 255], dtype=np.uint8)
    lower2 = np.array([160, 50, 50], dtype=np.uint8)
    upper2 = np.array([180, 255, 255], dtype=np.uint8)
    mask1 = cv2.inRange(hsv, lower1, upper1)
    mask2 = cv2.inRange(hsv, lower2, upper2)
    mask = cv2.bitwise_or(mask1, mask2)
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    if num_labels <= 1:
        return mask.astype(bool)
    h, w = mask.shape[:2]
    cx, cy = w / 2.0, h / 2.0
    scores = []
    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        px, py = centroids[i]
        dist = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
        scores.append(area / (dist + 1.0))
    idx = int(np.argmax(scores)) + 1
    mask = labels == idx
    mask_u8 = (mask.astype(np.uint8)) * 255
    contours, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        cnt = max(contours, key=cv2.contourArea)
        if len(cnt) >= 5:
            ellipse = cv2.fitEllipse(cnt)
            ell_mask = np.zeros_like(mask_u8)
            cv2.ellipse(ell_mask, ellipse, 255, -1)
            mask = ell_mask.astype(bool)
    return mask


def green_mask_rgb(color_np):
    hsv = cv2.cvtColor(color_np, cv2.COLOR_RGB2HSV)
    lower = np.array([35, 50, 40], dtype=np.uint8)
    upper = np.array([95, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    if num_labels <= 1:
        return mask.astype(bool)
    h, w = mask.shape[:2]
    cx, cy = w / 2.0, h / 2.0
    scores = []
    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        px, py = centroids[i]
        dist = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
        scores.append(area / (dist + 1.0))
    idx = int(np.argmax(scores)) + 1
    return labels == idx


def clean_depth(depth, mask):
    depth = depth.astype(np.float32)
    depth[~mask] = 0
    depth = cv2.medianBlur(depth, 5)
    depth = cv2.bilateralFilter(depth, d=5, sigmaColor=25, sigmaSpace=25)
    return depth


def fill_depth_holes(depth, mask):
    depth_f = depth.copy().astype(np.float32)
    depth_f[~mask] = 0
    kernel = np.ones((5, 5), np.uint8)
    for _ in range(3):
        dilated = cv2.dilate(depth_f, kernel, iterations=1)
        holes = (depth_f == 0) & (mask)
        depth_f[holes] = dilated[holes]
    return depth_f


def filter_depth_outliers(depth, mask):
    vals = depth[mask]
    vals = vals[np.isfinite(vals) & (vals > 0)]
    if vals.size == 0:
        return mask
    med = np.median(vals)
    mad = np.median(np.abs(vals - med))
    if mad == 0:
        mad = np.std(vals) if vals.size > 1 else 1.0
    low = med - 2.5 * mad
    high = med + 2.5 * mad
    depth_mask = (depth >= low) & (depth <= high) & (depth > 0)
    return mask & depth_mask


def remove_outliers(points, k=12, std_ratio=2.0):
    if points.size == 0:
        return points
    # 对大规模点云跳过 O(n^2) 的两两距离计算，避免内存爆炸。
    if len(points) < k or len(points) > 3000:
        return points
    diff = points[:, None, :] - points[None, :, :]
    dist = np.sqrt(np.sum(diff * diff, axis=2))
    dist.sort(axis=1)
    mean_dist = dist[:, 1 : k + 1].mean(axis=1)
    m = mean_dist.mean()
    s = mean_dist.std() if mean_dist.size > 1 else 0
    keep = mean_dist <= (m + std_ratio * s)
    return points[keep]


def align_pointclouds_icp(pointclouds):
    if not pointclouds:
        return np.empty((0, 3), dtype=np.float32)
    ref = pointclouds[0]
    merged = ref.copy()
    for i in range(1, len(pointclouds)):
        src = pointclouds[i]
        if src.size == 0 or merged.size == 0:
            continue
        src_c = np.mean(src, axis=0)
        dst_c = np.mean(merged, axis=0)
        aligned = src + (dst_c - src_c)
        merged = np.vstack([merged, aligned])
    return merged


def refine_mask_center(mask):
    ys, xs = np.where(mask)
    if ys.size == 0:
        return mask
    cy = float(np.mean(ys))
    cx = float(np.mean(xs))
    ry = float(np.max(np.abs(ys - cy))) + 1.0
    rx = float(np.max(np.abs(xs - cx))) + 1.0
    yy, xx = np.indices(mask.shape)
    dy = (yy - cy) / ry
    dx = (xx - cx) / rx
    r2 = dx * dx + dy * dy
    center_mask = r2 <= 0.9
    return mask & center_mask


def sfm_camera_pose(rgb_images, K):
    detector = cv2.AKAZE_create()
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

    poses = []
    prev_kp = None
    prev_des = None
    for i, img in enumerate(rgb_images):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        mask = (np.any(img != 0, axis=2).astype(np.uint8)) * 255
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)

        kp, des = detector.detectAndCompute(gray, mask=mask)
        if i == 0:
            pose = np.eye(4)
            poses.append(pose)
            prev_kp, prev_des = kp, des
            continue
        if des is None or prev_des is None:
            poses.append(poses[-1])
            prev_kp, prev_des = kp, des
            continue

        matches = matcher.match(des, prev_des)
        matches = sorted(matches, key=lambda x: x.distance)
        good = [m for m in matches if m.distance < 30]
        if len(good) < 10:
            poses.append(poses[-1])
            prev_kp, prev_des = kp, des
            continue

        src_pts = np.float32([kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst_pts = np.float32([prev_kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        E, mask_e = cv2.findEssentialMat(
            src_pts,
            dst_pts,
            K,
            method=cv2.RANSAC,
            prob=0.999,
            threshold=0.8,
            maxIters=10000,
        )
        if mask_e is None:
            poses.append(poses[-1])
            prev_kp, prev_des = kp, des
            continue
        inliers = mask_e.ravel().nonzero()[0]
        if len(inliers) < 10:
            poses.append(poses[-1])
            prev_kp, prev_des = kp, des
            continue

        src_in = src_pts[inliers]
        dst_in = dst_pts[inliers]
        _, R, t, _ = cv2.recoverPose(E, src_in, dst_in, K)
        pose = np.eye(4)
        pose[:3, :3] = R
        pose[:3, 3] = t.flatten()
        poses.append(pose)
        prev_kp, prev_des = kp, des

    return poses


def depth_to_pointcloud(depth_img, pose, K, mask=None):
    h, w = depth_img.shape
    u, v = np.meshgrid(np.arange(w), np.arange(h))
    z = depth_img.astype(np.float32)
    valid = z > 0
    if mask is not None:
        valid &= mask
    u = u[valid]
    v = v[valid]
    z = z[valid]

    x_cam = (u - K[0, 2]) * z / K[0, 0]
    y_cam = (v - K[1, 2]) * z / K[1, 1]
    points_cam = np.stack([x_cam, y_cam, z], axis=1)

    pose_inv = np.linalg.inv(pose)
    points_world = pose_inv[:3, :3] @ points_cam.T + pose_inv[:3, 3:4]
    return points_world.T


def voxel_downsample(points, voxel_size):
    if points.size == 0:
        return points
    coords = np.floor(points / voxel_size).astype(np.int64)
    _, idx = np.unique(coords, axis=0, return_index=True)
    return points[idx]


def merge_pointclouds(pointclouds, voxel_size=5.0):
    merged = np.vstack(pointclouds) if pointclouds else np.empty((0, 3), dtype=np.float32)
    merged = voxel_downsample(merged, voxel_size)
    return merged


def write_ply(path, points, colors=None):
    with open(path, "w", encoding="utf-8") as f:
        f.write("ply\n")
        f.write("format ascii 1.0\n")
        f.write(f"element vertex {len(points)}\n")
        f.write("property float x\n")
        f.write("property float y\n")
        f.write("property float z\n")
        if colors is not None:
            f.write("property uchar red\n")
            f.write("property uchar green\n")
            f.write("property uchar blue\n")
        f.write("end_header\n")
        for i, p in enumerate(points):
            if colors is None:
                f.write(f"{p[0]} {p[1]} {p[2]}\n")
            else:
                r, g, b = colors[i]
                f.write(f"{p[0]} {p[1]} {p[2]} {int(r)} {int(g)} {int(b)}\n")


def texture_sampling(points, image_dir, K):
    images = sorted([f for f in os.listdir(image_dir) if f.lower().endswith((".png", ".jpg", ".jpeg"))])
    if not images or points.size == 0:
        return None
    colors_acc = []
    for name in images:
        img = read_image_unicode(os.path.join(image_dir, name))
        if img is None:
            continue
        pts = points.T
        proj = K @ pts
        z = proj[2, :]
        z[z == 0] = 1e-8
        uv = (proj[:2, :] / z).T.astype(int)
        h, w = img.shape[:2]
        cols = []
        for u, v in uv:
            if 0 <= u < w and 0 <= v < h:
                bgr = img[v, u]
                cols.append([bgr[2], bgr[1], bgr[0]])
            else:
                cols.append([0, 0, 0])
        colors_acc.append(np.array(cols))
    if not colors_acc:
        return None
    colors = np.mean(colors_acc, axis=0)
    return colors


def load_ply_points(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            line = f.readline()
            if not line.startswith("ply"):
                return None
            fmt = ""
            vertex_count = 0
            while True:
                line = f.readline()
                if not line:
                    return None
                if line.startswith("format"):
                    fmt = line.strip()
                if line.startswith("element vertex"):
                    vertex_count = int(line.strip().split()[-1])
                if line.strip() == "end_header":
                    break
            if "ascii" not in fmt:
                return None
            data = []
            for _ in range(vertex_count):
                line = f.readline()
                if not line:
                    break
                parts = line.strip().split()
                if len(parts) < 3:
                    continue
                x, y, z = map(float, parts[:3])
                data.append([x, y, z])
            if not data:
                return np.empty((0, 3), dtype=np.float32)
            return np.array(data, dtype=np.float32)
    except Exception:
        return None


def load_ply_points_colors(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            line = f.readline()
            if not line.startswith("ply"):
                return None, None
            fmt = ""
            vertex_count = 0
            has_color = False
            while True:
                line = f.readline()
                if not line:
                    return None, None
                if line.startswith("format"):
                    fmt = line.strip()
                if line.startswith("element vertex"):
                    vertex_count = int(line.strip().split()[-1])
                if line.startswith("property uchar red"):
                    has_color = True
                if line.strip() == "end_header":
                    break
            if "ascii" not in fmt:
                return None, None
            pts = []
            cols = []
            for _ in range(vertex_count):
                line = f.readline()
                if not line:
                    break
                parts = line.strip().split()
                if len(parts) < 3:
                    continue
                x, y, z = map(float, parts[:3])
                pts.append([x, y, z])
                if has_color and len(parts) >= 6:
                    r, g, b = map(float, parts[3:6])
                    cols.append([r, g, b])
            pts = np.array(pts, dtype=np.float32) if pts else np.empty((0, 3), dtype=np.float32)
            if has_color and cols:
                cols = np.array(cols, dtype=np.float32)
                return pts, cols
            return pts, None
    except Exception:
        return None, None


def pointcloud_volume(points, voxel_size=1.0):
    if points is None or points.size == 0:
        return 0.0
    coords = np.floor(points / voxel_size).astype(np.int64)
    _, idx = np.unique(coords, axis=0, return_index=True)
    num_voxels = len(idx)
    return float(num_voxels) * (voxel_size ** 3)


def measure_from_depth(depth_image_path, fx, fy):
    depth_image = cv2.imread(depth_image_path, cv2.IMREAD_ANYDEPTH)
    if depth_image is None:
        return None, None, None
    mask = (depth_image > 0).astype(np.uint8) * 255
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if len(contours) == 0:
        return None, None, None
    cnt = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(cnt)
    center_x = x + w // 2
    center_y = y + h // 2
    depth = depth_image[center_y, center_x]
    if depth == 0:
        return None, None, None
    diameter = (w * depth) / fx
    height = (h * depth) / fy
    return depth, diameter, height


def reconstruct_sfm(color_dir, depth_dir, output_dir, fx, fy, cx, cy, max_pairs=None, mask=None):
    rgb_files = sorted([f for f in os.listdir(color_dir) if f.lower().endswith((".png", ".jpg", ".jpeg"))])
    depth_files = sorted([f for f in os.listdir(depth_dir) if f.lower().endswith((".png", ".jpg", ".jpeg"))])
    if max_pairs:
        rgb_files = rgb_files[:max_pairs]
        depth_files = depth_files[:max_pairs]

    rgb_images = [read_image_unicode(os.path.join(color_dir, f)) for f in rgb_files]
    depth_images = [read_image_unicode(os.path.join(depth_dir, f), cv2.IMREAD_ANYDEPTH) for f in depth_files]
    rgb_images = [img for img in rgb_images if img is not None]
    depth_images = [img for img in depth_images if img is not None]
    if not rgb_images or not depth_images:
        raise RuntimeError("未能读取彩色图或深度图。")

    K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float32)
    poses = sfm_camera_pose(rgb_images, K)

    pointclouds = []
    for i, depth in enumerate(depth_images):
        pose = poses[i]
        mask_i = mask
        if mask_i is None and i < len(rgb_images):
            rgb = cv2.cvtColor(rgb_images[i], cv2.COLOR_BGR2RGB)
            mask_r = red_mask_rgb(rgb)
            mask_g = green_mask_rgb(rgb)
            mask_i = mask_g if np.count_nonzero(mask_g) >= np.count_nonzero(mask_r) else mask_r
            mask_i = refine_mask_center(mask_i)
        if mask_i is not None:
            depth = clean_depth(depth, mask_i)
            depth = fill_depth_holes(depth, mask_i)
            mask_i = filter_depth_outliers(depth, mask_i)
            vals = depth[mask_i]
            vals = vals[np.isfinite(vals) & (vals > 0)]
            if vals.size > 8:
                p5, p95 = np.percentile(vals, [5, 95])
                mask_i = mask_i & (depth >= p5) & (depth <= p95)
        points = depth_to_pointcloud(depth, pose, K, mask=mask_i)
        pointclouds.append(points)

    merged = align_pointclouds_icp(pointclouds)
    pre_n = len(merged)
    voxel_size = 2.0 if pre_n > 300000 else 1.0
    merged = merge_pointclouds([merged], voxel_size=voxel_size)
    merged = remove_outliers(merged, k=12, std_ratio=3.0)
    os.makedirs(output_dir, exist_ok=True)
    out_ply = os.path.join(output_dir, "reconstructed_sfm_fruit.ply")
    write_ply(out_ply, merged)

    colors = texture_sampling(merged, color_dir, K)
    out_tex = os.path.join(output_dir, "reconstructed_sfm_fruit_color.ply")
    if colors is not None:
        write_ply(out_tex, merged, colors=colors)
    return out_ply, out_tex
