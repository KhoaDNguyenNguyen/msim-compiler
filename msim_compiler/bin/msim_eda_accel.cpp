#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <queue>
#include <algorithm>
#include <limits>

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
            if (owner > 0 && owner != net_id) {
                move_cost += cross_penalty + congestion_grid[cell] * 8;
            } else if (owner == net_id) {
                move_cost = std::max(1, step_cost / 2);
            } else {
                move_cost += congestion_grid[cell];
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

}
