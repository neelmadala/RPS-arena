// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title RPSArena — commit-reveal rock-paper-scissors with stakes
/// @notice Flow:
///   1. Creator calls createMatch(commitment) with the stake as msg.value.
///      commitment = keccak256(abi.encodePacked(address(this), creator, uint8(move), salt))
///   2. Joiner calls joinMatch(id, move) with the same stake. The joiner's move is public,
///      which is safe because the creator's move is already locked in by the commitment.
///   3. Creator calls reveal(id, move, salt) before the deadline. The winner is paid.
///   4. If the creator never reveals, the joiner calls claimTimeout(id) and wins the pot.
///      (A creator who sees they lost and refuses to reveal simply forfeits.)
///   Open matches nobody has joined can be cancelled by the creator for a refund.
contract RPSArena {
    enum Move { None, Rock, Paper, Scissors }
    enum Status { None, Open, Joined, Settled, Cancelled }

    struct Match {
        address creator;
        address joiner;
        uint256 stake;
        bytes32 commitment;
        Move creatorMove;
        Move joinerMove;
        Status status;
        uint64 revealDeadline;
    }

    uint64 public immutable revealWindow;
    uint256 public nextMatchId = 1;
    mapping(uint256 => Match) public matches;
    /// @notice Payouts that could not be pushed (e.g. receiver reverted) can be pulled here.
    mapping(address => uint256) public credits;

    bool private locked;

    event MatchCreated(uint256 indexed id, address indexed creator, uint256 stake);
    event MatchJoined(uint256 indexed id, address indexed joiner, Move joinerMove, uint64 revealDeadline);
    event MatchSettled(
        uint256 indexed id,
        address indexed winner, // address(0) for a draw
        Move creatorMove,
        Move joinerMove,
        bool forfeit
    );
    event MatchCancelled(uint256 indexed id);
    event CreditAdded(address indexed account, uint256 amount);
    event Withdrawn(address indexed account, uint256 amount);

    error InvalidMove();
    error WrongStatus();
    error WrongStake();
    error NotCreator();
    error NotJoiner();
    error CannotPlaySelf();
    error BadReveal();
    error DeadlineNotPassed();
    error DeadlinePassed();
    error NothingToWithdraw();
    error Reentrancy();

    modifier nonReentrant() {
        if (locked) revert Reentrancy();
        locked = true;
        _;
        locked = false;
    }

    constructor(uint64 _revealWindow) {
        revealWindow = _revealWindow == 0 ? 10 minutes : _revealWindow;
    }

    /// @notice Helper so clients can build the exact commitment the contract expects.
    function makeCommitment(address player, Move move, bytes32 salt) public view returns (bytes32) {
        return keccak256(abi.encodePacked(address(this), player, uint8(move), salt));
    }

    function createMatch(bytes32 commitment) external payable returns (uint256 id) {
        if (commitment == bytes32(0)) revert BadReveal();
        id = nextMatchId++;
        Match storage m = matches[id];
        m.creator = msg.sender;
        m.stake = msg.value;
        m.commitment = commitment;
        m.status = Status.Open;
        emit MatchCreated(id, msg.sender, msg.value);
    }

    function joinMatch(uint256 id, Move move) external payable {
        Match storage m = matches[id];
        if (m.status != Status.Open) revert WrongStatus();
        if (move == Move.None || uint8(move) > 3) revert InvalidMove();
        if (msg.value != m.stake) revert WrongStake();
        if (msg.sender == m.creator) revert CannotPlaySelf();

        m.joiner = msg.sender;
        m.joinerMove = move;
        m.status = Status.Joined;
        m.revealDeadline = uint64(block.timestamp) + revealWindow;
        emit MatchJoined(id, msg.sender, move, m.revealDeadline);
    }

    function reveal(uint256 id, Move move, bytes32 salt) external nonReentrant {
        Match storage m = matches[id];
        if (m.status != Status.Joined) revert WrongStatus();
        if (msg.sender != m.creator) revert NotCreator();
        if (block.timestamp > m.revealDeadline) revert DeadlinePassed();
        if (move == Move.None) revert InvalidMove();
        if (makeCommitment(msg.sender, move, salt) != m.commitment) revert BadReveal();

        m.creatorMove = move;
        m.status = Status.Settled;

        uint256 pot = m.stake * 2;
        int8 result = _outcome(move, m.joinerMove); // 1 creator wins, -1 joiner wins, 0 draw
        address winner;
        if (result == 1) {
            winner = m.creator;
            _pay(m.creator, pot);
        } else if (result == -1) {
            winner = m.joiner;
            _pay(m.joiner, pot);
        } else {
            _pay(m.creator, m.stake);
            _pay(m.joiner, m.stake);
        }
        emit MatchSettled(id, winner, move, m.joinerMove, false);
    }

    function claimTimeout(uint256 id) external nonReentrant {
        Match storage m = matches[id];
        if (m.status != Status.Joined) revert WrongStatus();
        if (msg.sender != m.joiner) revert NotJoiner();
        if (block.timestamp <= m.revealDeadline) revert DeadlineNotPassed();

        m.status = Status.Settled;
        _pay(m.joiner, m.stake * 2);
        emit MatchSettled(id, m.joiner, Move.None, m.joinerMove, true);
    }

    function cancelMatch(uint256 id) external nonReentrant {
        Match storage m = matches[id];
        if (m.status != Status.Open) revert WrongStatus();
        if (msg.sender != m.creator) revert NotCreator();
        m.status = Status.Cancelled;
        _pay(m.creator, m.stake);
        emit MatchCancelled(id);
    }

    function withdraw() external nonReentrant {
        uint256 amount = credits[msg.sender];
        if (amount == 0) revert NothingToWithdraw();
        credits[msg.sender] = 0;
        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "withdraw failed");
        emit Withdrawn(msg.sender, amount);
    }

    function getMatch(uint256 id) external view returns (Match memory) {
        return matches[id];
    }

    /// @dev Push payment with a gas cap; if the receiver reverts, fall back to a pull credit
    ///      so a hostile contract can never block settlement.
    function _pay(address to, uint256 amount) private {
        if (amount == 0) return;
        (bool ok, ) = to.call{value: amount, gas: 30_000}("");
        if (!ok) {
            credits[to] += amount;
            emit CreditAdded(to, amount);
        }
    }

    function _outcome(Move a, Move b) private pure returns (int8) {
        if (a == b) return 0;
        if (
            (a == Move.Rock && b == Move.Scissors) ||
            (a == Move.Paper && b == Move.Rock) ||
            (a == Move.Scissors && b == Move.Paper)
        ) return 1;
        return -1;
    }
}
