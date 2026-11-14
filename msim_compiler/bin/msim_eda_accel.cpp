#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <queue>
#include <thread>
#include <atomic>
#include <algorithm>
#include <limits>
#if defined(__AVX2__) || defined(_M_AVX2)
#include <immintrin.h>
#define MSIM_USE_AVX2 1
#endif

#ifdef _WIN32
#define MSIM_API __declspec(dllexport)
#else
#define MSIM_API __attribute__((visibility("default")))
#endif

struct GridNodeState {
    int32_t cost_f;
    int32_t cost_g;
    int32_t x;
    int32_t y;
    int32_t dir;
    bool operator>(const GridNodeState& other) const {
        return cost_f > other.cost_f;
    }
};

struct BatchRouteJob {
    int32_t net_id;
    int32_t sx;
    int32_t sy;
    int32_t slx;
    int32_t sly;
    int32_t tx;
    int32_t ty;
    int32_t tlx;
    int32_t tly;
};

static inline int32_t fast_logistic_penalty(int32_t occupancy, int32_t cap = 4, int32_t max_pen = 240) {
    float z = 1.35f * static_cast<float>(occupancy - cap);
    if (z < -15.0f) return 0;
    if (z > 15.0f) return max_pen;
    return static_cast<int32_t>(std::lround(static_cast<float>(max_pen) / (1.0f + std::exp(-z))));
}

static inline double simd_dot_product(int32_t n, const double* a, const double* b) {
    double sum = 0.0;
    int32_t i = 0;
#ifdef MSIM_USE_AVX2
    __m256d acc = _mm256_setzero_pd();
    for (; i + 4 <= n; i += 4) {
        __m256d va = _mm256_loadu_pd(a + i);
        __m256d vb = _mm256_loadu_pd(b + i);
        acc = _mm256_add_pd(acc, _mm256_mul_pd(va, vb));
    }
    alignas(32) double tmp[4];
    _mm256_store_pd(tmp, acc);
    sum = tmp[0] + tmp[1] + tmp[2] + tmp[3];
#endif
    for (; i < n; ++i) {
        sum += a[i] * b[i];
    }
    return sum;
}

extern "C" {

MSIM_API int32_t astar_orthogonal_route(
    int32_t grid_w,
    int32_t grid_h,
    const int32_t* obstacle_grid,
    const int32_t* congestion_grid,
    const int32_t* net_owner_grid,
    int32_t net_id,
    int32_t sx,
    int32_t sy,
    int32_t s_lead_x,
    int32_t s_lead_y,
    int32_t tx,
    int32_t ty,
    int32_t t_lead_x,
    int32_t t_lead_y,
    int32_t step_cost,
    int32_t bend_penalty,
    int32_t cross_penalty,
    int32_t* out_path_xy,
    int32_t max_path_pts
) {
    if (sx < 0 || sx >= grid_w || sy < 0 || sy >= grid_h ||
        tx < 0 || tx >= grid_w || ty < 0 || ty >= grid_h) {
        return 0;
    }

    const int32_t INF = std::numeric_limits<int32_t>::max() / 4;
    const int32_t total_cells = grid_w * grid_h;
    const int32_t total_states = total_cells * 5;

    std::vector<int32_t> dist(total_states, INF);
    std::vector<int32_t> parent_state(total_states, -1);

    auto idx_of = [grid_w](int32_t x, int32_t y, int32_t d) -> int32_t {
        return ((y * grid_w + x) * 5) + d;
    };

    auto heuristic = [t_lead_x, t_lead_y, step_cost](int32_t x, int32_t y) -> int32_t {
        return (std::abs(x - t_lead_x) + std::abs(y - t_lead_y)) * step_cost;
    };

    std::priority_queue<GridNodeState, std::vector<GridNodeState>, std::greater<GridNodeState>> pq;

    int32_t init_dir = 4;
    if (s_lead_x > sx) init_dir = 0;
    else if (s_lead_x < sx) init_dir = 1;
    else if (s_lead_y > sy) init_dir = 2;
    else if (s_lead_y < sy) init_dir = 3;

    int32_t start_state = idx_of(s_lead_x, s_lead_y, init_dir);
    dist[start_state] = step_cost;
    pq.push({step_cost + heuristic(s_lead_x, s_lead_y), step_cost, s_lead_x, s_lead_y, init_dir});

    const int32_t dx[4] = {1, -1, 0, 0};
    const int32_t dy[4] = {0, 0, 1, -1};

    int32_t goal_state = -1;

    while (!pq.empty()) {
        GridNodeState cur = pq.top();
        pq.pop();

        int32_t cur_idx = idx_of(cur.x, cur.y, cur.dir);
        if (cur.cost_g > dist[cur_idx]) continue;

        if (cur.x == t_lead_x && cur.y == t_lead_y) {
            goal_state = cur_idx;
            break;
        }

        for (int32_t nd = 0; nd < 4; ++nd) {
            int32_t nx = cur.x + dx[nd];
            int32_t ny = cur.y + dy[nd];
            if (nx < 0 || nx >= grid_w || ny < 0 || ny >= grid_h) continue;

            int32_t cell = ny * grid_w + nx;
            bool is_target_lead = (nx == t_lead_x && ny == t_lead_y);
            if (obstacle_grid[cell] > 0 && !is_target_lead) continue;

            int32_t move_cost = step_cost;
            if (cur.dir != 4 && cur.dir != nd) {
                move_cost += bend_penalty;
            }

            int32_t owner = net_owner_grid[cell];
            int32_t sig_pen = fast_logistic_penalty(congestion_grid[cell]);
            if (owner > 0 && owner != net_id) {
                move_cost += cross_penalty + sig_pen;
            } else if (owner == net_id) {
                move_cost = std::max(1, step_cost / 2);
            } else {
                move_cost += sig_pen / 4;
            }

            int32_t next_g = cur.cost_g + move_cost;
            int32_t next_idx = idx_of(nx, ny, nd);
            if (next_g < dist[next_idx]) {
                dist[next_idx] = next_g;
                parent_state[next_idx] = cur_idx;
                int32_t next_f = next_g + heuristic(nx, ny);
                pq.push({next_f, next_g, nx, ny, nd});
            }
        }
    }

    if (goal_state < 0) {
        return 0;
    }

    std::vector<std::pair<int32_t, int32_t>> rev_pts;
    rev_pts.push_back({tx, ty});
    int32_t trace = goal_state;
    while (trace >= 0) {
        int32_t rem = trace / 5;
        int32_t py = rem / grid_w;
        int32_t px = rem % grid_w;
        rev_pts.push_back({px, py});
        trace = parent_state[trace];
    }
    rev_pts.push_back({sx, sy});
    std::reverse(rev_pts.begin(), rev_pts.end());

    std::vector<std::pair<int32_t, int32_t>> simplified;
    for (const auto& pt : rev_pts) {
        if (simplified.empty() || simplified.back() != pt) {
            if (simplified.size() >= 2) {
                auto p1 = simplified[simplified.size() - 2];
                auto p2 = simplified[simplified.size() - 1];
                if ((p1.first == p2.first && p2.first == pt.first) ||
                    (p1.second == p2.second && p2.second == pt.second)) {
                    simplified.back() = pt;
                    continue;
                }
            }
            simplified.push_back(pt);
        }
    }

    int32_t count = std::min(static_cast<int32_t>(simplified.size()), max_path_pts);
    for (int32_t i = 0; i < count; ++i) {
        out_path_xy[i * 2] = simplified[i].first;
        out_path_xy[i * 2 + 1] = simplified[i].second;
    }
    return count;
}

MSIM_API int32_t cluster_spatial_nodes_simd(
    int32_t num_nodes,
    const float* x_coords,
    const float* y_coords,
    int32_t* out_labels,
    float epsilon,
    int32_t min_pts
) {
    if (num_nodes <= 0) return 0;

    const float eps_sq = epsilon * epsilon;
    std::vector<std::vector<int32_t>> adj(num_nodes);

    unsigned int n_threads = std::max(1u, std::thread::hardware_concurrency());
    std::vector<std::thread> pool;
    std::atomic<int32_t> next_row{0};

    for (unsigned int t = 0; t < n_threads; ++t) {
        pool.emplace_back([&]() {
            while (true) {
                int32_t i = next_row.fetch_add(1, std::memory_order_relaxed);
                if (i >= num_nodes) break;
                std::vector<int32_t> local_nbrs;
                float xi = x_coords[i];
                float yi = y_coords[i];
                for (int32_t j = 0; j < num_nodes; ++j) {
                    float dx = xi - x_coords[j];
                    float dy = yi - y_coords[j];
                    if ((dx * dx + dy * dy) <= eps_sq) {
                        local_nbrs.push_back(j);
                    }
                }
                adj[i] = std::move(local_nbrs);
            }
        });
    }
    for (auto& th : pool) th.join();

    std::vector<int32_t> labels(num_nodes, -1);
    std::queue<int32_t> bfs_q;
    int32_t cluster_id = 0;

    for (int32_t p = 0; p < num_nodes; ++p) {
        if (labels[p] != -1) continue;
        if (static_cast<int32_t>(adj[p].size()) < min_pts) {
            labels[p] = 0;
            continue;
        }
        ++cluster_id;
        labels[p] = cluster_id;
        for (int32_t nb : adj[p]) {
            if (nb != p) bfs_q.push(nb);
        }
        while (!bfs_q.empty()) {
            int32_t q = bfs_q.front();
            bfs_q.pop();
            if (labels[q] == 0) labels[q] = cluster_id;
            if (labels[q] != -1) continue;
            labels[q] = cluster_id;
            if (static_cast<int32_t>(adj[q].size()) >= min_pts) {
                for (int32_t nb2 : adj[q]) {
                    if (labels[nb2] == -1 || labels[nb2] == 0) {
                        bfs_q.push(nb2);
                    }
                }
            }
        }
    }

    for (int32_t i = 0; i < num_nodes; ++i) {
        out_labels[i] = labels[i] < 0 ? 0 : labels[i];
    }
    return cluster_id;
}

MSIM_API int32_t solve_laplacian_cg_simd(
    int32_t n,
    const double* laplacian_mat,
    const double* rhs_vec,
    double* out_sol,
    int32_t max_iter,
    double tol
) {
    if (n <= 0) return 0;
    std::vector<double> x(n, 0.0);
    std::vector<double> r(n, 0.0);
    std::vector<double> z(n, 0.0);
    std::vector<double> p(n, 0.0);
    std::vector<double> Ap(n, 0.0);
    std::vector<double> inv_diag(n, 1.0);

    for (int32_t i = 0; i < n; ++i) {
        double d = laplacian_mat[i * n + i];
        inv_diag[i] = (std::fabs(d) > 1e-12) ? (1.0 / d) : 1.0;
        r[i] = rhs_vec[i];
        z[i] = inv_diag[i] * r[i];
        p[i] = z[i];
    }

    unsigned int hw_threads = std::max(1u, std::thread::hardware_concurrency());
    bool use_mt = (n >= 64 && hw_threads > 1);

    auto mat_vec = [&](const std::vector<double>& vec_in, std::vector<double>& vec_out) {
        if (!use_mt) {
            for (int32_t i = 0; i < n; ++i) {
                vec_out[i] = simd_dot_product(n, laplacian_mat + (i * n), vec_in.data());
            }
        } else {
            std::atomic<int32_t> row_cursor{0};
            std::vector<std::thread> workers;
            for (unsigned int t = 0; t < hw_threads; ++t) {
                workers.emplace_back([&]() {
                    while (true) {
                        int32_t r_idx = row_cursor.fetch_add(1, std::memory_order_relaxed);
                        if (r_idx >= n) break;
                        vec_out[r_idx] = simd_dot_product(n, laplacian_mat + (r_idx * n), vec_in.data());
                    }
                });
            }
            for (auto& w : workers) w.join();
        }
    };

    double rz_old = simd_dot_product(n, r.data(), z.data());
    int32_t iter = 0;
    for (; iter < max_iter; ++iter) {
        if (std::sqrt(simd_dot_product(n, r.data(), r.data())) < tol) break;
        mat_vec(p, Ap);
        double pAp = simd_dot_product(n, p.data(), Ap.data());
        if (std::fabs(pAp) < 1e-15) break;
        double alpha = rz_old / pAp;
        for (int32_t i = 0; i < n; ++i) {
            x[i] += alpha * p[i];
            r[i] -= alpha * Ap[i];
        }
        if (std::sqrt(simd_dot_product(n, r.data(), r.data())) < tol) break;
        for (int32_t i = 0; i < n; ++i) {
            z[i] = inv_diag[i] * r[i];
        }
        double rz_new = simd_dot_product(n, r.data(), z.data());
        double beta = rz_new / rz_old;
        for (int32_t i = 0; i < n; ++i) {
            p[i] = z[i] + beta * p[i];
        }
        rz_old = rz_new;
    }

    for (int32_t i = 0; i < n; ++i) out_sol[i] = x[i];
    return iter;
}

MSIM_API int32_t solve_quadratic_placement_mt(
    int32_t num_parts,
    const double* adjacency_weights,
    const double* anchor_weights,
    const double* anchor_x,
    const double* anchor_y,
    int32_t grid_pitch,
    int32_t* out_snapped_x,
    int32_t* out_snapped_y
) {
    if (num_parts <= 0) return 0;
    std::vector<double> sys_mat(num_parts * num_parts, 0.0);
    std::vector<double> rhs_x(num_parts, 0.0);
    std::vector<double> rhs_y(num_parts, 0.0);
    std::vector<double> sol_x(num_parts, 0.0);
    std::vector<double> sol_y(num_parts, 0.0);

    for (int32_t i = 0; i < num_parts; ++i) {
        double deg = anchor_weights[i];
        for (int32_t j = 0; j < num_parts; ++j) {
            if (i != j) {
                double w = adjacency_weights[i * num_parts + j];
                sys_mat[i * num_parts + j] = -w;
                deg += w;
            }
        }
        sys_mat[i * num_parts + i] = std::max(1e-6, deg);
        rhs_x[i] = anchor_weights[i] * anchor_x[i];
        rhs_y[i] = anchor_weights[i] * anchor_y[i];
    }

    int32_t it_x = 0, it_y = 0;
    std::thread tx([&]() {
        it_x = solve_laplacian_cg_simd(num_parts, sys_mat.data(), rhs_x.data(), sol_x.data(), 256, 1e-7);
    });
    std::thread ty([&]() {
        it_y = solve_laplacian_cg_simd(num_parts, sys_mat.data(), rhs_y.data(), sol_y.data(), 256, 1e-7);
    });
    tx.join();
    ty.join();

    double pitch = static_cast<double>(std::max(1, grid_pitch));
    for (int32_t i = 0; i < num_parts; ++i) {
        out_snapped_x[i] = static_cast<int32_t>(std::lround(sol_x[i] / pitch)) * grid_pitch;
        out_snapped_y[i] = static_cast<int32_t>(std::lround(sol_y[i] / pitch)) * grid_pitch;
    }
    return it_x + it_y;
}

}
